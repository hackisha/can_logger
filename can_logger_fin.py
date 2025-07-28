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
SERVICE_ACCOUNT_KEY_PATH = '/home/pi/can_logger/serviceAccountKey.json'
FIREBASE_DB_URL = 'https://emucanlogger-default-rtdb.firebaseio.com/'
FIREBASE_DB_PATH = 'emu_realtime_data'

# --- GPIO 설정 ---
BUTTON_PIN = 21

# --- CAN ID 설정 ---
EMU_ID_BASE = 0x600
EMU_IDS = { f"FRAME_{i}": EMU_ID_BASE + i for i in range(8) }

# --- 로그 디렉터리 ---
LOG_DIR = "/home/pi/can_logger/logs"
os.makedirs(LOG_DIR, exist_ok=True)
# ==============================================================================

# --- 전역 변수 (Global Variables) ---
logging_active = False
exit_program = False
csv_writer = None
csv_file = None

# --- 파서 함수들 (Parser Functions) ---
# (기존 코드와 동일)
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
    """버튼이 눌렸을 때 호출되는 함��"""
    global logging_active, csv_writer, csv_file
    time.sleep(0.1) # 디바운스
    if GPIO.input(channel) == GPIO.LOW:
        logging_active = not logging_active
        if logging_active:
            print("\nButton pressed: Starting logging...")
            if csv_file is None:
                csv_filename = f"{LOG_DIR}/emu_log_all_{datetime.now().strftime('%Y%m%d_%H%M%S')}.csv"
                print(f"New log file: {csv_filename}")
                csv_file = open(csv_filename, 'w', newline='')
                # 고정된 필드 이름 사용
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
            print("\nButton pressed: Stopping logging...")
            if csv_file is not None:
                csv_file.close()
                print(f"Log file saved.")
                csv_file = None
                csv_writer = None

# --- 종료 신호 처리 함수 ---
def handle_exit(signum, frame):
    """Ctrl+C 또는 시스템 종료 신호 처리"""
    global exit_program
    print("\nExit signal received. Shutting down.")
    exit_program = True

# --- 메인 함수 ---
def main():
    global exit_program, csv_file

    # --- CAN 인터페이스 설정 ---
    print("Configuring CAN interface...")
    os.system('sudo ifconfig can0 down')
    os.system('sudo ip link set can0 type can bitrate 1000000')
    os.system('sudo ifconfig can0 up')
    time.sleep(0.5) # 인터페이스가 안정될 때까지 잠시 대기

    # --- GPIO 설정 ---
    GPIO.setmode(GPIO.BCM)
    GPIO.setup(BUTTON_PIN, GPIO.IN, pull_up_down=GPIO.PUD_UP)
    GPIO.add_event_detect(BUTTON_PIN, GPIO.FALLING, callback=button_callback, bouncetime=500)
    
    # --- 종료 신호 처리 설정 ---
    signal.signal(signal.SIGINT, handle_exit)
    signal.signal(signal.SIGTERM, handle_exit)

    # --- Firebase 초기화 ---
    try:
        if not os.path.exists(SERVICE_ACCOUNT_KEY_PATH):
            print(f"Error: Firebase service account key not found at: {SERVICE_ACCOUNT_KEY_PATH}", file=sys.stderr)
            sys.exit(1)
        cred = credentials.Certificate(SERVICE_ACCOUNT_KEY_PATH)
        firebase_admin.initialize_app(cred, {'databaseURL': FIREBASE_DB_URL})
        db_ref = db.reference(FIREBASE_DB_PATH)
        print("Firebase initialized successfully.")
    except Exception as e:
        print(f"Error initializing Firebase: {e}", file=sys.stderr)
        sys.exit(1)

    # --- CAN 버스 초기화 ---
    try:
        bus = can.interface.Bus(channel='can0', interface='socketcan')
        print("CAN bus initialized successfully.")
    except Exception as e:
        print(f"Error initializing CAN bus: {e}", file=sys.stderr)
        sys.exit(1)

    print("Program started. Press the button to start/stop logging.")
    print("Press Ctrl+C to exit the program completely.")

    parsers = {EMU_ID_BASE + i: globals()[f"parse_emu_frame_{i}"] for i in range(8)}
    latest_data = {}
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

        # FRAME_0을 수신하고 모든 프레임이 한 번 이상 수신되었을 때
        if msg.arbitration_id == EMU_IDS["FRAME_0"] and received_ids_in_cycle.issuperset(expected_ids):
            ts = datetime.now().strftime("%Y-%m-%d %H:%M:%S.%f")[:-3]
            latest_data["Timestamp"] = ts
            
            # Firebase에 데이터 전송
            try:
                db_ref.update(latest_data)
            except Exception as e:
                print(f"\nFirebase upload failed: {e}", file=sys.stderr)

            # CLI에 현재 상태 출력
            print(f"[{ts}] RPM:{latest_data.get('RPM', 0):>5} | MAP:{latest_data.get('MAP_kPa', 0):>3}kPa | TPS:{latest_data.get('TPS_percent', 0):>5.1f}% | CLT:{latest_data.get('CLT_C', 0):>4}°C | Speed:{latest_data.get('VSS_kmh', 0):>3}km/h")
            
            # 로깅이 활성화 상태이고, CSV 파일이 준비되었을 때만 기록
            if logging_active and csv_writer is not None:
                csv_writer.writerow(latest_data)
                if csv_file:
                    csv_file.flush()

            received_ids_in_cycle.clear()

    # --- 프로그램 종료 처리 ---
    bus.shutdown()
    print("\nCAN bus shut down.")
    if csv_file is not None:
        csv_file.close()
        print("Log file saved.")

if __name__ == "__main__":
    try:
        main()
    finally:
        GPIO.cleanup()
        print("GPIO cleaned up. Program finished.")