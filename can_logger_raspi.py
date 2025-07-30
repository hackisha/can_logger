import can
import struct
import csv
from datetime import datetime
import sys
import os
import time
import firebase_admin
from firebase_admin import credentials, db
import signal
import threading

# ==============================================================================
# === 설정 (Configuration) ===
# ==============================================================================
# --- Firebase 설정 ---
SERVICE_ACCOUNT_KEY_PATH = '/home/pi/can_logger/serviceAccountKey.json'
FIREBASE_DB_URL = 'https://emucanlogger-default-rtdb.firebaseio.com/'
FIREBASE_DB_PATH = 'emu_realtime_data'
UPLOAD_INTERVAL_SEC = 0.2  # Firebase 업로드 주기 (초)

# --- CAN 설정 ---
CAN_CHANNEL = 'can0'
CAN_BITRATE = 500000

# --- CAN ID 설정 ---
EMU_ID_BASE = 0x600
EMU_IDS = { f"FRAME_{i}": EMU_ID_BASE + i for i in range(8) }

# --- 로그 디렉터리 및 주기 ---
LOG_DIR = "/home/pi/can_logger/logs"
CSV_LOG_INTERVAL_SEC = 0.1 # CSV 로깅 주기 (초)
# ==============================================================================

# --- 전역 변수 (Global Variables) ---
exit_event = threading.Event() # 프로그램 종료를 위한 스레드 안전 이벤트
latest_data = {}
latest_data_lock = threading.Lock() # latest_data 접근을 위한 락

# --- 파서 함수들 (기존과 동일) ---
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

# --- Firebase 업로더 스레드 함수 ---
def firebase_uploader(db_ref):
    """주기적으로 Firebase에 최신 데이터를 업로드합니다."""
    while not exit_event.is_set():
        try:
            with latest_data_lock:
                if latest_data: # 데이터가 있을 때만 업로드
                    data_to_upload = latest_data.copy()
                else:
                    data_to_upload = None
            
            if data_to_upload:
                db_ref.update(data_to_upload)

        except Exception as e:
            print(f"\nFirebase 업로드 스레드 오류: {e}", file=sys.stderr)
        
        exit_event.wait(UPLOAD_INTERVAL_SEC)

# --- CSV 로거 스레드 함수 ---
def csv_logger(csv_writer, csv_file):
    """주기적으로 최신 데이터를 CSV 파일에 기록합니다."""
    while not exit_event.is_set():
        try:
            with latest_data_lock:
                if latest_data:
                    data_to_log = latest_data.copy()
                else:
                    data_to_log = None

            if data_to_log:
                data_to_log["Timestamp"] = datetime.now().strftime("%Y-%m-%d %H:%M:%S.%f")[:-3]
                csv_writer.writerow(data_to_log)

        except Exception as e:
            print(f"\nCSV 로거 스레드 오류: {e}", file=sys.stderr)

        exit_event.wait(CSV_LOG_INTERVAL_SEC)


# --- 종료 신호 처리 함수 ---
def handle_exit(signum, frame):
    print("\n종료 신호 수신. 프로그램을 안전하게 종료합니다.")
    exit_event.set()

# --- 메인 로직 실행 함수 ---
def run_logger():
    global latest_data
    bus = None
    csv_file = None
    try:
        # --- 시스템 설정 확인 및 안내 ---
        print("SocketCAN (can0) 인터페이스를 확인합니다...")
        if not os.path.exists("/sys/class/net/can0"):
            print("오류: can0 인터페이스를 찾을 수 없습니다.", file=sys.stderr)
            sys.exit(1)
        print("can0 인터페이스가 확인되었습니다.")

        # --- 리소스 초기화 ---
        os.makedirs(LOG_DIR, exist_ok=True)

        cred = credentials.Certificate(SERVICE_ACCOUNT_KEY_PATH)
        firebase_admin.initialize_app(cred, {'databaseURL': FIREBASE_DB_URL})
        db_ref = db.reference(FIREBASE_DB_PATH)
        print("Firebase 초기화 성공.")

        bus = can.interface.Bus(channel=CAN_CHANNEL, interface='socketcan', bitrate=CAN_BITRATE)
        print("CAN 버스 (SocketCAN) 초기화 성공.")

        # --- CSV 파일 설정 ---
        csv_filename = f"{LOG_DIR}/emu_log_all_{datetime.now().strftime('%Y%m%d_%H%M%S')}.csv"
        print(f"로그 파일: {csv_filename}")
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

        # --- 스레드 시작 ---
        uploader_thread = threading.Thread(target=firebase_uploader, args=(db_ref,), daemon=True)
        uploader_thread.start()
        print(f"Firebase 업로드 스레드 시작 (주기: {UPLOAD_INTERVAL_SEC}초).")

        logger_thread = threading.Thread(target=csv_logger, args=(csv_writer, csv_file), daemon=True)
        logger_thread.start()
        print(f"CSV 로거 스레드 시작 (주기: {CSV_LOG_INTERVAL_SEC}초).")


        print("\n로깅을 시작합니다. 종료하려면 Ctrl+C 를 누르세요.")

        parsers = {
            EMU_ID_BASE + 0: parse_emu_frame_0,
            EMU_ID_BASE + 1: parse_emu_frame_1,
            EMU_ID_BASE + 2: parse_emu_frame_2,
            EMU_ID_BASE + 3: parse_emu_frame_3,
            EMU_ID_BASE + 4: parse_emu_frame_4,
            EMU_ID_BASE + 5: parse_emu_frame_5,
            EMU_ID_BASE + 6: parse_emu_frame_6,
            EMU_ID_BASE + 7: parse_emu_frame_7,
        }

        # --- 메인 루프 ---
        while not exit_event.is_set():
            msg = bus.recv(timeout=0.1)
            if msg is None: continue
            
            parser = parsers.get(msg.arbitration_id)
            if not parser: continue

            parsed_values = parser(msg.data)
            if not parsed_values: continue
            
            with latest_data_lock:
                latest_data.update(parsed_values)

            # 화면은 매 메시지 수신 시마다 즉시 갱신
            ts_for_display = datetime.now().strftime("%H:%M:%S.%f")[:-3]
            with latest_data_lock:
                # 화면 출력 시에도 락을 사용하여 데이터 일관성 유지
                rpm = latest_data.get('RPM', 0)
                map_kpa = latest_data.get('MAP_kPa', 0)
                tps = latest_data.get('TPS_percent', 0)
                clt = latest_data.get('CLT_C', 0)
                vss = latest_data.get('VSS_kmh', 0)

            print(f"[{ts_for_display}] RPM:{rpm:>5} | MAP:{map_kpa:>3}kPa | TPS:{tps:>5.1f}% | CLT:{clt:>4}°C | Speed:{vss:>3}km/h", end='\r')


    except FileNotFoundError:
        print(f"\n오류: Firebase 서비스 계정 키 파일을 찾을 수 없습니다: {SERVICE_ACCOUNT_KEY_PATH}", file=sys.stderr)
    except can.CanError as e:
        print(f"\nCAN 통신 오류: {e}", file=sys.stderr)
        print(f"CAN 인터페이스가 활성화(up) 상태인지 확인하세요: `sudo ip link set {CAN_CHANNEL} up type can bitrate {CAN_BITRATE}`", file=sys.stderr)
    except Exception as e:
        print(f"\n프로그램 실행 중 오류 발생: {e}", file=sys.stderr)
    finally:
        # --- 리소스 정리 ---
        exit_event.set() # 모든 스레드에 종료 신호 전송
        
        # 스레드가 완전히 종료될 때까지 잠시 대기
        if 'uploader_thread' in locals() and uploader_thread.is_alive():
            uploader_thread.join(timeout=1)
        if 'logger_thread' in locals() and logger_thread.is_alive():
            logger_thread.join(timeout=1)

        if bus:
            bus.shutdown()
            print("\nCAN 버스가 종료되었습니다.")
        if csv_file is not None:
            csv_file.close()
            print(f"로그 파일이 저장되었습니다: {csv_file.name}")
        print("프로그램이 완전히 종료되었습니다.")

if __name__ == "__main__":
    signal.signal(signal.SIGINT, handle_exit)
    signal.signal(signal.SIGTERM, handle_exit)
    run_logger()
