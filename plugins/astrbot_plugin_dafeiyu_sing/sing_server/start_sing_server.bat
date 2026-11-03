@echo off
rem 启动大肥鱼唱歌服务（默认 http://127.0.0.1:18620），这个窗口别关
set ROOT=%~dp0
if exist D:\dafeiyu\sing\venv set ROOT=D:\dafeiyu\sing\
"%ROOT%venv\Scripts\python.exe" "%ROOT%sing_server.py" --soulx "%ROOT%SoulX-Singer" --model "%ROOT%models\SoulX-Singer\model.pt"
pause
