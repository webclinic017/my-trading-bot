
## Go into venv trading-bot
`source trading-bot/bin/activate`

## install ta-lib
https://www.youtube.com/watch?v=AQFZMvYp2KA

`wget https://prdownloads.sourceforge.net/ta-lib/ta-lib-0.4.0-src.tar.gz`
`tar -xvf ta-lib-0.4.0-src.tar.gz`
`cd ta-lib`
`./configure --prefix=/usr`
`make`
`sudo make install`
`whereis ta-lib`
`pip3 install ta-lib`

`python > import talib as ta` (for testing)

## Install all dependencies
`pip install -r requirements.txt`

## Start server
`python main.py`