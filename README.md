## 초기세팅
[Unit]
Description=CAN Logger Service with CAN Interface Setup
After=network-online.target
Wants=network-online.target

[Service]
Type=simple
User=root
WorkingDirectory=/home/pi/can_logger


ExecStartPre=/sbin/ip link set can0 type can bitrate 1000000
ExecStartPre=/sbin/ip link set can0 up


ExecStart=/usr/bin/python3 /home/pi/can_logger/can_logger_gpio.py

StandardOutput=inherit
StandardError=inherit
Restart=always

[Install]
WantedBy=multi-user.target





sudo systemctl daemon-reload
서비스 재시작 (변경된 설정으로 즉시 실행):
sudo systemctl restart can-logger.service
상태 확인
sudo systemctl status can-logger.service
