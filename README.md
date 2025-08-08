## can 인터페이스 수동 초기화
sudo ifconfig can0 down
sudo ip link set can0 type can bitrate 1000000
sudo ifconfig can0 up

# 초기세팅
## can_logger.service
부팅시 파이썬 스크립트 실행
## can-start.service, can-start.sh
부팅시 CAN 인터페이스 초기화


# systemd 사용법

- systemctl restart <서비스명>: ex systemctl restart can_logger.service
- systemctl status <서비스명>: 서비스의 상태를 확인 
- systemctl start <서비스명>: 서비스를 시작
- systemctl restart <서비스명>: 서비스를 재시작
- systemctl is-active <서비스명>: 서비스가 활성화 상태인지 확인
- systemctl is-enabled <서비스명>: 서비스가 부팅 시 자동으로 시작되도록 설정되어 있는지 확인
- systemctl list-units --type=service: 현재 실행 중인 모든 서비스를 나열
- systemctl list-unit-files --type=service

## 부팅시 실행할 스크립트
sudo vim /lib/systemd/system/can_logger.service

```bash
sudo systemctl daemon-reload
sudo systemctl restart can_logger.service
sudo systemctl status can_logger.service
```
chmod +x 로 start-can.sh, can_logger.service, can-start.service 권한 줘야함
