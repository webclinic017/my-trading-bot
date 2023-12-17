"""Module providing a function of FastAPI and WebSocket."""
import json
from app.crud.trade_history import create_trade_history
from fastapi import (
    Depends,
    FastAPI,
    HTTPException,
    Request,
    WebSocket,
    WebSocketDisconnect,
    status,
    Response,
)
from fastapi.middleware.cors import CORSMiddleware
from fastapi.staticfiles import StaticFiles
from app.src.schema.schemas import (
    BotErrorSchema,
    LoginForm,
    TokenSchema,
    TradeHistoryCreate,
)
from app.src.controller.trade import get_order_realizedPnl
from app.src.schema import schemas
from app.crud.bot_error import create_error_log
from app.crud.user import create_user, get_user_by_email
from app.utils.deps import get_current_user
from .routers import backtests, users, bots, strategies, workers
from app.models import Base
from .src.config.database import SessionLocal, engine, get_db
from starlette.websockets import WebSocketDisconnect
from typing import Any, Dict, List
import logging
from sqlalchemy.orm import Session
from starlette.responses import FileResponse
import os
from app.utils.auth import (
    verify_password,
    create_access_token,
    create_refresh_token,
)
from contextlib import asynccontextmanager
from app.utils.redis import get_redis_client

current_dir = os.path.dirname(os.path.abspath(__file__))
frontend_dir = os.path.join(current_dir, "../dist")
Base.metadata.create_all(bind=engine)


@asynccontextmanager
async def lifespan(app: FastAPI):
    try:
        # startup
        app.state.redis = await get_redis_client()
        yield
        # shutdown
        await app.state.redis.close()
    except Exception as e:
        logging.error("Redis connection error")


app = FastAPI(lifespan=lifespan)
# Dependency


API_VER = "v1"
app.include_router(users.router, prefix=f"/api/{API_VER}/users", tags=["User"])
app.include_router(bots.router, prefix=f"/api/{API_VER}/bots", tags=["Bot"])
app.include_router(
    backtests.router, prefix=f"/api/{API_VER}/backtests", tags=["Backtest"]
)
app.include_router(
    strategies.router, prefix=f"/api/{API_VER}/strategies", tags=["Strategy"]
)
app.include_router(
    workers.router, prefix=f"/api/{API_VER}/worker-servers", tags=["Worker-Server"]
)
app.mount("/static", StaticFiles(directory=frontend_dir), name="static")

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# data = [
#     {"pnl": -6.66192551, "timestamp": 1702469949000},
#     {"pnl": -6.645022917790003, "timestamp": 1702469952000},
# ]


async def read_item(key, cb):
    value = await app.state.redis.get(key)
    if value is None:
        # get value from cb function
        data = cb
        value = json.dumps(data)
        await app.state.redis.set(key, value)
        print("set redis", key)

    return json.loads(value)


@app.get(
    "/api/v1/user/profile",
    summary="Get details of currently logged in user to authenticate user",
    response_model=schemas.UserPublic,
)
async def get_me(user: schemas.User = Depends(get_current_user)):
    return user


@app.post("/signup", response_model=schemas.User)
def create_new_user(user: schemas.UserCreate, db: Session = Depends(get_db)):
    try:
        db_user = get_user_by_email(db, email=user.email)
        if db_user:
            raise HTTPException(status_code=400, detail="Email already registered")
        return create_user(db=db, user=user)
    except HTTPException as http_ex:
        raise http_ex
    except Exception as e:
        print(e)
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=str(e) or "Something broke when creating user!",
        )


@app.post(
    "/login",
    summary="Create access and refresh tokens for user",
    response_model=TokenSchema,
)
async def login(
    response: Response, form_data: LoginForm, db: Session = Depends(get_db)
):
    try:
        user = get_user_by_email(db, form_data.email)
        if user is None:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="Incorrect email or password",
            )

        print("user want to login", user.name, user.email)
        hashed_pass = user.hashed_password
        if not verify_password(form_data.password, hashed_pass):
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="Incorrect email or password",
            )

        access_token = create_access_token(username=user.name, email=user.email)
        refresh_token = create_refresh_token(username=user.name, email=user.email)

        return {
            "access_token": access_token,
            "refresh_token": refresh_token,
            "user_id": user.id,
            "username": user.name,
        }
    except HTTPException as http_ex:
        raise http_ex
    except Exception as e:
        print(e)
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=str(e) or "Something broke when login or creating JWT token!",
        )


@app.get("/{full_path:path}")
async def serve_frontend(full_path: str, request: Request):
    # Check if the file exists in the static directory
    static_file_path = os.path.join(frontend_dir, full_path)
    if os.path.isfile(static_file_path):
        return FileResponse(static_file_path)
    # Fallback to serving index.html for SPA routing
    index_file = os.path.join(frontend_dir, "index.html")
    return FileResponse(index_file)


@app.websocket("/ws/trade_history")
async def websocket_endpoint(websocket: WebSocket):
    await websocket.accept()
    try:
        while True:
            data = await websocket.receive_json()
            await websocket.send_json("Message received")

            # check if the trading action message
            if data.get("action"):
                try:
                    with SessionLocal() as db:
                        realizedPnl = None
                        if data["data"]["side"] == "SELL":
                            # get the realized pnl
                            realizedPnl = get_order_realizedPnl(
                                data["data"]["orderId"], data["data"]["symbol"]
                            )
                        trade_data = TradeHistoryCreate(**data)

                        db_trade_history = create_trade_history(
                            db, trade_data, realizedPnl
                        )
                        db.commit()
                        print("store: ", db_trade_history)
                except Exception as e:
                    print(f"Error: {e}")

            elif data.get("error"):
                try:
                    print("error", data)
                    with SessionLocal() as db:
                        error_data = BotErrorSchema(**data)
                        error = create_error_log(error_data, db)
                        db.commit()
                        print("error: ", error)
                except Exception as e:
                    print(f"Error logging failed: {e}")
            else:
                print("message", data)

    except WebSocketDisconnect:
        print("Client disconnected")


class ConnectionManager:
    def __init__(self):
        self.active_connections: Dict[int, List[WebSocket]] = {}

    async def connect(self, client_id: int, websocket: WebSocket):
        await websocket.accept()
        if client_id not in self.active_connections:
            self.active_connections[client_id] = []
        self.active_connections[client_id].append(websocket)
        print("append client", client_id, websocket)

    def disconnect(self, client_id: int, websocket: WebSocket):
        if websocket in self.active_connections.get(client_id):
            self.active_connections[client_id].remove(websocket)
            if not self.active_connections.get(client_id):
                # If the list is empty, remove the client_id from the dictionary
                del self.active_connections[client_id]

    async def send_personal_message(self, message: Any, client_id: int):
        for websocket in self.active_connections.get(client_id):
            await websocket.send_json(message)


manager = ConnectionManager()

@app.websocket("/ws/backtest_result/{client_id}")
async def websocket_backtest_result_endpoint(websocket: WebSocket, client_id: int):
    await manager.connect(client_id, websocket)
    try:
        while True:
            data = await websocket.receive_json()
            await manager.send_personal_message(data, client_id)
    except WebSocketDisconnect:
        manager.disconnect(client_id, websocket)
        print(f"Client #{client_id} left the chat")
