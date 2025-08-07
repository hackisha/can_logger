
import can
import struct
import csv
from datetime import datetime
import sys
import os
import time
import threading
import signal
import socket
import serial
import pynmea2

# GPIO 라이브러리 임포트 (테스트 환경 고려)
try:
    import RPi.GPIO as GPIO
    IS_RASPI = True
except (ImportError, RuntimeError):
    IS_RASPI = False

# ==============================================================================
# === 설정 (Configuration) ===
# ==============================================================================
# --- CAN 설정 ---
CAN_CHANNEL = 'can0'
CAN_BITRATE = 1000000

# --- GPS 설정 ---
SERIAL_PORT = "/dev/serial0"
BAUD_RATE = 9600

# --- CAN ID 설정 ---
EMU_ID_BASE = 0x600
EMU_IDS = { f"FRAME_{i}": EMU_ID_BASE + i for i in range(8) }

# --- 로그 디렉터리 ---
LOG_DIR = "/home/pi/can_logger/logs"

# --- GPIO 핀 설정 (BCM 모드 기준) ---
BUTTON_PIN = 17       # 로깅 시작/종료 버튼 (폴링 방식)
LOGGING_LED_PIN = 27  # 로깅 상태 LED (데이터 기록 시 점멸)
ERROR_LED_PIN = 22    # 오류 발생 표시 LED
WIFI_LED_PIN = 5      # Wi-Fi 연결 상태 LED
# ==============================================================================

# --- 전역 변수 (Global Variables) ---
exit_event = threading.Event()
latest_can_data = {}
latest_gps_data = {}
logging_active = False
error_occurred = False
csv_writer = None
csv_file = None

# --- CAN 파서 함수들 (기존과 동일) ---
def parse_emu_frame_0(data):
    if len(data) != 8: return {}
    return {"RPM": struct.unpack_from('<H', data, 0)[0], "TPS_percent": data[2] * 0.5, "IAT_C": struct.unpack_from('b', data, 3)[0], "MAP_kPa": struct.unpack_from('<H', data, 4)[0], "PulseWidth_ms": struct.unpack_from('<H', data, 6)[0] * 0.016129}
def parse_emu_frame_1(data):
    if len(data) != 8: return {}
    return {"AnalogIn1_V": struct.unpack_from('<H', data, 0)[0] * 0.0048828125, "AnalogIn2_V": struct.unpack_from('<H', data, 2)[0] * 0.0048828125, "AnalogIn3_V": struct.unpack_from('<H', data, 4)[0] * 0.0048828125, "AnalogIn4_V": struct.unpack_from('<H', data, 6)[0] * 0.0048828125}
def parse_emu_frame_2(data):
    if len(data) != 8: return {}
    return {"VSS_kmh": struct.unpack_from('<H', data, 0)[0], "Baro_kPa": data[2], "OilTemp_C": data[3], "OilPressure_bar": data[4] * 0.0625, "FuelPressure_bar": data[5] * 0.0625, "CLT_C": struct.unpack_from('<h', data, 6)[0]}
def parse_emu_frame_3(data):
    if len(data) != 8: return {}
    return {"IgnAngle_deg": struct.unpack_from('b', data, 0)[0] * 0.5, "DwellTime_ms": data[1] * 0.05, "WBO_Lambda": data[2] * 0.0078125, "LambdaCorrection_percent": data[3] * 0.5, "EGT1_C": struct.unpack_from('<H', data, 4)[0], "EGT2_C": struct.unpack_from('<H', data, 6)[0]}
def parse_emu_frame_4(data):
    if len(data) != 8: return {}
    return {"Gear": data[0], "EmuTemp_C": data[1], "Batt_V": struct.unpack_from('<H', data, 2)[0] * 0.027, "CEL_Error": struct.unpack_from('<H', data, 4)[0], "Flags1": data[6], "Ethanol_percent": data[7]}
def parse_emu_frame_5(data):
    if len(data) != 8: return {}
    return {"DBW_Pos_percent": data[0] * 0.5, "DBW_Target_percent": data[1] * 0.5, "TC_drpm_raw": struct.unpack_from('<H', data, 2)[0], "TC_drpm": struct.unpack_from('<H', data, 4)[0], "TC_TorqueReduction_percent": data[6], "PitLimit_TorqueReduction_percent": data[7]}
def parse_emu_frame_6(data):
    if len(data) != 8: return {}
    return {"AnalogIn5_V": struct.unpack_from('<H', data, 0)[0] * 0.0048828125, "AnalogIn6_V": struct.unpack_from('<H', data, 2)[0] * 0.0048828125, "OutFlags1": data[4], "OutFlags2": data[5], "OutFlags3": data[6], "OutFlags4": data[7]}
def parse_emu_frame_7(data):
    parsed_data = {"BoostTarget_kPa": struct.unpack_from('<H', data, 0)[0], "PWM1_DC_percent": data[2], "DSG_Mode": data[3]}
    if len(data) == 8:
        parsed_data.update({"LambdaTarget": data[4] * 0.01, "PWM2_DC_percent": data[5], "FuelUsed_L": struct.unpack_from('<H', data, 6)[0] * 0.01})
    return parsed_data

# --- 스레드 및 제어 함수 ---
def setup_gpio():
    if not IS_RASPI: return
    GPIO.setmode(GPIO.BCM)
    GPIO.setup(BUTTON_PIN, GPIO.IN, pull_up_down=GPIO.PUD_UP)
    GPIO.setup(LOGGING_LED_PIN, GPIO.OUT, initial=GPIO.LOW)
    GPIO.setup(ERROR_LED_PIN, GPIO.OUT, initial=GPIO.LOW)
    GPIO.setup(WIFI_LED_PIN, GPIO.OUT, initial=GPIO.LOW)
    print("GPIO 초기화 완료. (폴링 방식)")

def toggle_logging_state():
    global logging_active, csv_writer, csv_file
    logging_active = not logging_active
    
    if logging_active:
        print("\n버튼 감지: 로깅을 시작합니다.")
        if IS_RASPI: GPIO.output(LOGGING_LED_PIN, GPIO.HIGH)
        
        csv_filename = f"{LOG_DIR}/datalog_{datetime.now().strftime('%Y%m%d_%H%M%S')}.csv"
        print(f"새 로그 파일: {csv_filename}")
        csv_file = open(csv_filename, 'w', newline='')
        
        # --- CSV 헤더에 GPS 필드 추가 ---
        fieldnames = [
            "Timestamp", "Latitude", "Longitude", "GPS_Speed_KPH", "Satellites",
            "RPM", "TPS_percent", "IAT_C", "MAP_kPa", "PulseWidth_ms",
            "AnalogIn1_V", "AnalogIn2_V", "AnalogIn3_V", "AnalogIn4_V",
            "VSS_kmh", "Baro_kPa", "OilTemp_C", "OilPressure_bar", "FuelPressure_bar", "CLT_C",
            "IgnAngle_deg", "DwellTime_ms", "WBO_Lambda", "LambdaCorrection_percent", "EGT1_C", "EGT2_C",
            "Gear", "EmuTemp_C", "Batt_V", "CEL_Error", "Flags1", "Ethanol_percent",
            "DBW_Pos_percent", "DBW_Target_percent", "TC_drpm_raw", "TC_drpm", "TC_TorqueReduction_percent", "PitLimit_TorqueReduction_percent",
            "AnalogIn5_V", "AnalogIn6_V", "OutFlags1", "OutFlags2", "OutFlags3", "OutFlags4",
            "BoostTarget_kPa", "PWM1_DC_percent", "DSG_Mode", "LambdaTarget", "PWM2_DC_percent", "FuelUsed_L"
        ]
        csv_writer = csv.DictWriter(csv_file, fieldnames=fieldnames, extrasaction='ignore')
        csv_writer.writeheader()
    else:
        print("\n버튼 감지: 로깅을 중지합니다.")
        if IS_RASPI: GPIO.output(LOGGING_LED_PIN, GPIO.LOW)
        
        if csv_file:
            csv_file.close()
            print(f"로그 파일 저장 완료: {csv_file.name}")
            csv_file = None
            csv_writer = None

def check_wifi_connection():
    while not exit_event.is_set():
        try:
            socket.create_connection(("8.8.8.8", 53), timeout=2)
            if IS_RASPI: GPIO.output(WIFI_LED_PIN, GPIO.HIGH)
        except OSError:
            if IS_RASPI: GPIO.output(WIFI_LED_PIN, GPIO.LOW)
        exit_event.wait(10)

def gps_reader_thread(ser):
    global latest_gps_data
    while not exit_event.is_set():
        try:
            line = ser.readline().decode('utf-8', errors='ignore')
            if line.startswith('$GPRMC'):
                msg = pynmea2.parse(line)
                if msg.status == 'A':
                    latest_gps_data = {
                        "Latitude": msg.latitude,
                        "Longitude": msg.longitude,
                        "GPS_Speed_KPH": msg.spd_over_grnd * 1.852 if msg.spd_over_grnd is not None else 0
                    }
            elif line.startswith('$GPGGA'):
                msg = pynmea2.parse(line)
                if msg.is_valid:
                    latest_gps_data["Satellites"] = msg.num_sats
        except (pynmea2.ParseError, serial.SerialException, UnicodeDecodeError):
            continue # 오류 발생 시 무시하고 계속 진행

def handle_exit(signum, frame):
    print("\n종료 신호 수신. 프로그램을 안전하게 종료합니다.")
    exit_event.set()

# --- 메인 로직 실행 함수 ---
def run_logger():
    global latest_can_data, error_occurred, csv_writer, csv_file
    bus = None
    ser = None
    last_press_time = 0
    
    try:
        setup_gpio()
        
        # --- CAN 인터페이스 자동 활성화 ---
        print(f"CAN 인터페이스({CAN_CHANNEL})를 설정합니다...")
        os.system(f'sudo ip link set {CAN_CHANNEL} down')
        if os.system(f'sudo ip link set {CAN_CHANNEL} up type can bitrate {CAN_BITRATE}') != 0:
            raise IOError(f"{CAN_CHANNEL} 인터페이스 활성화에 실패했습니다.")
        print("CAN 인터페이스 활성화 성공.")
        bus = can.interface.Bus(channel=CAN_CHANNEL, bustype='socketcan')
        print("CAN 버스 초기화 성공.")

        # --- GPS 시리얼 포트 초기화 및 스레드 시작 ---
        try:
            ser = serial.Serial(SERIAL_PORT, baudrate=BAUD_RATE, timeout=1)
            gps_thread = threading.Thread(target=gps_reader_thread, args=(ser,), daemon=True)
            gps_thread.start()
            print(f"GPS({SERIAL_PORT}) 수신 스레드 시작.")
        except serial.SerialException as e:
            print(f"경고: GPS 시리얼 포트({SERIAL_PORT})를 열 수 없습니다: {e}")

        # --- Wi-Fi 체크 스레드 시작 ---
        wifi_thread = threading.Thread(target=check_wifi_connection, daemon=True)
        wifi_thread.start()
        
        print("\n대기 중... 버튼을 눌러 로깅을 시작하세요.")
        print("프로그램을 완전히 종료하려면 Ctrl+C 를 누르세요.")

        parsers = {EMU_ID_BASE + i: globals()[f"parse_emu_frame_{i}"] for i in range(8)}

        while not exit_event.is_set():
            # 버튼 폴링
            current_time = time.time()
            if IS_RASPI and GPIO.input(BUTTON_PIN) == GPIO.LOW:
                if (current_time - last_press_time) > 0.3:
                    toggle_logging_state()
                last_press_time = current_time

            # 로깅 활성화 시 CAN 메시지 처리
            if logging_active and csv_writer:
                msg = bus.recv(timeout=0.02)
                if msg is None: continue
                
                parser = parsers.get(msg.arbitration_id)
                if not parser: continue

                parsed_values = parser(msg.data)
                if not parsed_values: continue
                
                latest_can_data.update(parsed_values)

                if msg.arbitration_id == EMU_IDS["FRAME_0"]:
                    # --- CAN과 GPS 데이터를 합쳐서 한 줄로 기록 ---
                    full_data_row = {"Timestamp": datetime.now().strftime("%Y-%m-%d %H:%M:%S.%f")[:-3]}
                    full_data_row.update(latest_gps_data)
                    full_data_row.update(latest_can_data)
                    
                    csv_writer.writerow(full_data_row)
                    csv_file.flush()
                    
                    if IS_RASPI:
                        GPIO.output(LOGGING_LED_PIN, GPIO.LOW)
                        time.sleep(0.05)
                        GPIO.output(LOGGING_LED_PIN, GPIO.HIGH)
            else:
                time.sleep(0.05)

    except Exception as e:
        print(f"\n프로그램 실행 중 심각한 오류 발생: {e}", file=sys.stderr)
        error_occurred = True
        if IS_RASPI: GPIO.output(ERROR_LED_PIN, GPIO.HIGH)
    
    finally:
        exit_event.set()
        if bus: bus.shutdown()
        if ser and ser.is_open: ser.close()
        if csv_file and not csv_file.closed: csv_file.close()
        if IS_RASPI: GPIO.cleanup()
        print("\n모든 리소스 정리 완료. 프로그램이 완전히 종료되었습니다.")

if __name__ == "__main__":
    if os.geteuid() != 0:
        print("오류: 이 스크립트는 sudo 권한으로 실행해야 합니다.")
        sys.exit(1)
        
    signal.signal(signal.SIGINT, handle_exit)
    signal.signal(signal.SIGTERM, handle_exit)
    run_logger()
