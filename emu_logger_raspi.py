import can
import struct
import csv
from datetime import datetime
import sys
import os
import time
import firebase_admin
from firebase_admin import credentials, db
import RPi.GPIO as GPIO
import signal

# ==============================================================================
# === 설정 (Configuration) ===
# ==============================================================================
# --- Firebase 설정 ---
# 중요: 이 파일은 실제 서비스 계정 키 파일의 경로를 가리켜야 합니다.
SERVICE_ACCOUNT_KEY_PATH = '/home/craft/can_logger/serviceAccountKey.json'
FIREBASE_DB_URL = 'https://emucanlogger-default-rtdb.firebaseio.com/'
FIREBASE_DB_PATH = 'emu_realtime_data'

# --- GPIO 설정 ---
BUTTON_PIN = 21  # 버튼이 연결된 GPIO 핀 번호 (BCM 모드)

# --- CAN 설정 ---
CAN_CHANNEL = 'can0'
CAN_BITRATE = 1000000  # 1Mbps. 사용하는 CAN 버스의 속도에 맞춰야 합니다.

# --- CAN ID 설정 ---
EMU_ID_BASE = 0x600
EMU_IDS = { f"FRAME_{i}": EMU_ID_BASE + i for i in range(8) }

# --- 로그 디렉터리 ---
LOG_DIR = "/home/craft/can_logger/logs"
os.makedirs(LOG_DIR, exist_ok=True)
# ==============================================================================

# --- 전역 변수 (Global Variables) ---
logging_active = False
exit_program = False
csv_writer = None
csv_file = None
latest_data = {}

# --- 파서 함수들 (Parser Functions) ---
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

# --- 버튼 콜백 함수 ---
def button_callback(channel):
    global logging_active, csv_writer, csv_file
    time.sleep(0.05) # 디바운스
    if GPIO.input(channel) == GPIO.HIGH: # 버튼이 눌리지 않은 상태면 리턴
        return

    logging_active = not logging_active
    if logging_active:
        print("\n버튼 입력: 로깅 시작...")
        if csv_file is None:
            csv_filename = f"{LOG_DIR}/emu_log_all_{datetime.now().strftime('%Y%m%d_%H%M%S')}.csv"
            print(f"새 로그 파일: {csv_filename}")
            csv_file = open(csv_filename, 'w', newline='')
            fieldnames = [
                "Timestamp", "RPM", "TPS_percent", "IAT_C", "MAP_kPa", "PulseWidth_ms",
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
        print("\n버튼 입력: 로깅 중지...")
        if csv_file is not None:
            csv_file.close()
            print(f"로그 파일 저장됨: {csv_file.name}")
            csv_file = None
            csv_writer = None

# --- 종료 신호 처리 함수 ---
def handle_exit(signum, frame):
    global exit_program
    print("\n종료 신호 수신. 프로그램을 안전하게 종료합니다.")
    exit_program = True

# --- 메인 함수 ---
def main():
    global exit_program, csv_file, latest_data

    # --- 종료 신호 처리 설정 ---
    signal.signal(signal.SIGINT, handle_exit)
    signal.signal(signal.SIGTERM, handle_exit)

    # --- GPIO 설정 ---
    GPIO.setwarnings(False)
    GPIO.setmode(GPIO.BCM)
    GPIO.setup(BUTTON_PIN, GPIO.IN, pull_up_down=GPIO.PUD_UP)
    GPIO.add_event_detect(BUTTON_PIN, GPIO.FALLING, callback=button_callback, bouncetime=300)
    
    # --- Firebase 초기화 ---
    try:
        if not os.path.exists(SERVICE_ACCOUNT_KEY_PATH):
            print(f"오류: Firebase 서비스 계정 키 파일을 찾을 수 없습니다: {SERVICE_ACCOUNT_KEY_PATH}", file=sys.stderr)
            sys.exit(1)
        cred = credentials.Certificate(SERVICE_ACCOUNT_KEY_PATH)
        firebase_admin.initialize_app(cred, {'databaseURL': FIREBASE_DB_URL})
        db_ref = db.reference(FIREBASE_DB_PATH)
        print("Firebase 초기화 성공.")
    except Exception as e:
        print(f"Firebase 초기화 오류: {e}", file=sys.stderr)
        sys.exit(1)

    # --- CAN 버스 초기화 ---
    try:
        # MCP2515 모듈을 위한 bustype='mcp2515' 사용
        bus = can.interface.Bus(channel=CAN_CHANNEL, bustype='mcp2515', bitrate=CAN_BITRATE)
        print("CAN 버스 초기화 성공.")
    except Exception as e:
        print(f"CAN 버스 초기화 중 오류가 발생했습니다: {e}", file=sys.stderr)
        print("스크립트를 실행하기 전에 다음 사항을 확인하세요:", file=sys.stderr)
        print("1. `sudo raspi-config`를 통해 SPI 인터페이스가 활성화되었는지 확인하세요.", file=sys.stderr)
        print("2. `/boot/config.txt` 파일에 MCP2515 오버레이 설정이 올바르게 추가되었는지 확인하세요.", file=sys.stderr)
        print(f"   (예: dtoverlay=mcp2515-can0,oscillator=8000000,interrupt=25,spimaxfrequency=20000000)", file=sys.stderr)
        print(f"3. `sudo ip link set {CAN_CHANNEL} up type can bitrate {CAN_BITRATE}` 명령어로 CAN 인터페이스를 활성화하세요.", file=sys.stderr)
        sys.exit(1)

    print("\n프로그램이 시작되었습니다. 버튼을 눌러 로깅을 시작/중지 할 수 있습니다.")
    print("프로그램을 완전히 종료하려면 Ctrl+C 를 누르세요.")

    parsers = {EMU_ID_BASE + i: globals()[f"parse_emu_frame_{i}"] for i in range(8)}
    expected_ids = set(parsers.keys())
    received_ids_in_cycle = set()

    while not exit_program:
        msg = bus.recv(timeout=0.1)
        if msg is None:
            continue

        parser = parsers.get(msg.arbitration_id)
        if not parser:
            continue

        parsed_values = parser(msg.data)
        if not parsed_values:
            continue
        
        for key, value in parsed_values.items():
            if isinstance(value, float):
                parsed_values[key] = float(f"{value:.3f}")
        
        latest_data.update(parsed_values)
        received_ids_in_cycle.add(msg.arbitration_id)

        if msg.arbitration_id == EMU_IDS["FRAME_0"] and received_ids_in_cycle.issuperset(expected_ids):
            ts = datetime.now().strftime("%Y-%m-%d %H:%M:%S.%f")[:-3]
            latest_data["Timestamp"] = ts
            
            try:
                db_ref.update(latest_data)
            except Exception as e:
                print(f"\nFirebase 업로드 실패: {e}", file=sys.stderr)

            print(f"[{ts}] RPM:{latest_data.get('RPM', 0):>5} | MAP:{latest_data.get('MAP_kPa', 0):>3}kPa | TPS:{latest_data.get('TPS_percent', 0):>5.1f}% | CLT:{latest_data.get('CLT_C', 0):>4}°C | Speed:{latest_data.get('VSS_kmh', 0):>3}km/h", end='\r')
            
            if logging_active and csv_writer is not None:
                csv_writer.writerow(latest_data)
                if csv_file:
                    csv_file.flush()

            received_ids_in_cycle.clear()

    # --- 프로그램 종료 처리 ---
    bus.shutdown()
    print("\nCAN 버스가 종료되었습니다.")
    if csv_file is not None:
        csv_file.close()
        print("로그 파일이 저장되었습니다.")

if __name__ == "__main__":
    try:
        main()
    finally:
        GPIO.cleanup()
        print("GPIO 리소스가 정리되었습니다. 프로그램이 완전히 종료되었습니다.")
