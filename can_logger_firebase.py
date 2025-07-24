import can
import struct
import csv
import threading
from datetime import datetime
import sys
import termios
import tty
import os
import time
import firebase_admin
from firebase_admin import credentials, db

# ==============================================================================
# === Firebase 설정 ===
# ==============================================================================
# 1. Firebase 서비스 계정 키 파일의 경로
#    (예: '/home/pi/can_logger/serviceAccountKey.json')
SERVICE_ACCOUNT_KEY_PATH = '/home/pi/can_logger/serviceAccountKey.json'

# 2. Firebase Realtime Database URL
FIREBASE_DB_URL = 'https://emucanlogger-default-rtdb.firebaseio.com/'

# 3. 데이터를 저장할 Firebase DB의 경로
FIREBASE_DB_PATH = 'emu_realtime_data'
# ==============================================================================

# CAN base ID as defined in EMU Black software
EMU_ID_BASE = 0x600

# All CAN frame IDs based on the base ID
EMU_IDS = {
    "FRAME_0": EMU_ID_BASE + 0,
    "FRAME_1": EMU_ID_BASE + 1,
    "FRAME_2": EMU_ID_BASE + 2,
    "FRAME_3": EMU_ID_BASE + 3,
    "FRAME_4": EMU_ID_BASE + 4,
    "FRAME_5": EMU_ID_BASE + 5,
    "FRAME_6": EMU_ID_BASE + 6,
    "FRAME_7": EMU_ID_BASE + 7,
}

# Log directory and file name setup
LOG_DIR = "./logs"
os.makedirs(LOG_DIR, exist_ok=True)
CSV_FILENAME = f"{LOG_DIR}/emu_log_all_{datetime.now().strftime('%Y%m%d_%H%M%S')}.csv"

exit_flag = False

# --- Parser Functions based on EMUcan.cpp ---

def parse_emu_frame_0(data):
    """Parses CAN frame (Base + 0)"""
    if len(data) != 8:
        return {}
    return {
        "RPM": struct.unpack_from('<H', data, 0)[0],
        "TPS_percent": data[2] * 0.5,
        "IAT_C": struct.unpack_from('b', data, 3)[0],
        "MAP_kPa": struct.unpack_from('<H', data, 4)[0],
        "PulseWidth_ms": struct.unpack_from('<H', data, 6)[0] * 0.016129,
    }

def parse_emu_frame_1(data):
    """Parses CAN frame (Base + 1)"""
    if len(data) != 8:
        return {}
    return {
        "AnalogIn1_V": struct.unpack_from('<H', data, 0)[0] * 0.0048828125,
        "AnalogIn2_V": struct.unpack_from('<H', data, 2)[0] * 0.0048828125,
        "AnalogIn3_V": struct.unpack_from('<H', data, 4)[0] * 0.0048828125,
        "AnalogIn4_V": struct.unpack_from('<H', data, 6)[0] * 0.0048828125,
    }

def parse_emu_frame_2(data):
    """Parses CAN frame (Base + 2)"""
    if len(data) != 8:
        return {}
    return {
        "VSS_kmh": struct.unpack_from('<H', data, 0)[0],
        "Baro_kPa": data[2],
        "OilTemp_C": data[3],
        "OilPressure_bar": data[4] * 0.0625,
        "FuelPressure_bar": data[5] * 0.0625,
        "CLT_C": struct.unpack_from('<h', data, 6)[0],
    }

def parse_emu_frame_3(data):
    """Parses CAN frame (Base + 3)"""
    if len(data) != 8:
        return {}
    return {
        "IgnAngle_deg": struct.unpack_from('b', data, 0)[0] * 0.5,
        "DwellTime_ms": data[1] * 0.05,
        "WBO_Lambda": data[2] * 0.0078125,
        "LambdaCorrection_percent": data[3] * 0.5,
        "EGT1_C": struct.unpack_from('<H', data, 4)[0],
        "EGT2_C": struct.unpack_from('<H', data, 6)[0],
    }

def parse_emu_frame_4(data):
    """Parses CAN frame (Base + 4)"""
    if len(data) != 8:
        return {}
    return {
        "Gear": data[0],
        "EmuTemp_C": data[1],
        "Batt_V": struct.unpack_from('<H', data, 2)[0] * 0.027,
        "CEL_Error": struct.unpack_from('<H', data, 4)[0],
        "Flags1": data[6],
        "Ethanol_percent": data[7],
    }

def parse_emu_frame_5(data):
    """Parses CAN frame (Base + 5)"""
    if len(data) != 8:
        return {}
    return {
        "DBW_Pos_percent": data[0] * 0.5,
        "DBW_Target_percent": data[1] * 0.5,
        "TC_drpm_raw": struct.unpack_from('<H', data, 2)[0],
        "TC_drpm": struct.unpack_from('<H', data, 4)[0],
        "TC_TorqueReduction_percent": data[6],
        "PitLimit_TorqueReduction_percent": data[7],
    }

def parse_emu_frame_6(data):
    """Parses CAN frame (Base + 6)"""
    if len(data) != 8:
        return {}
    return {
        "AnalogIn5_V": struct.unpack_from('<H', data, 0)[0] * 0.0048828125,
        "AnalogIn6_V": struct.unpack_from('<H', data, 2)[0] * 0.0048828125,
        "OutFlags1": data[4],
        "OutFlags2": data[5],
        "OutFlags3": data[6],
        "OutFlags4": data[7],
    }

def parse_emu_frame_7(data):
    """Parses CAN frame (Base + 7)"""
    parsed_data = {
        "BoostTarget_kPa": struct.unpack_from('<H', data, 0)[0],
        "PWM1_DC_percent": data[2],
        "DSG_Mode": data[3],
    }
    if len(data) == 8: # Data since version 143
        parsed_data.update({
            "LambdaTarget": data[4] * 0.01,
            "PWM2_DC_percent": data[5],
            "FuelUsed_L": struct.unpack_from('<H', data, 6)[0] * 0.01,
        })
    return parsed_data

# --- Keypress listener for graceful exit ---
def keypress_listener():
    global exit_flag
    fd = sys.stdin.fileno()
    old_settings = termios.tcgetattr(fd)
    try:
        tty.setcbreak(fd)
        while not exit_flag:
            if sys.stdin.read(1).lower() == 's':
                exit_flag = True
                break
            time.sleep(0.1)
    finally:
        termios.tcsetattr(fd, termios.TCSADRAIN, old_settings)

# --- Main logging loop ---
def main():
    global exit_flag

    # --- Firebase Initialization ---
    try:
        if not os.path.exists(SERVICE_ACCOUNT_KEY_PATH):
            print(f"Error: Firebase service account key not found at: {SERVICE_ACCOUNT_KEY_PATH}", file=sys.stderr)
            print("Please download the key from your Firebase project settings and place it in the correct path.", file=sys.stderr)
            sys.exit(1)
        
        cred = credentials.Certificate(SERVICE_ACCOUNT_KEY_PATH)
        firebase_admin.initialize_app(cred, {'databaseURL': FIREBASE_DB_URL})
        db_ref = db.reference(FIREBASE_DB_PATH)
        print("Firebase initialized successfully.")
    except Exception as e:
        print(f"Error initializing Firebase: {e}", file=sys.stderr)
        print("Please check your Firebase settings and service account key.", file=sys.stderr)
        sys.exit(1)

    # --- CAN Bus Initialization ---
    try:
        bus = can.interface.Bus(channel='can0', interface='socketcan')
    except Exception as e:
        print(f"Error initializing CAN bus: {e}", file=sys.stderr)
        sys.exit(1)

    print(f"Logging all EMU CAN data to {CSV_FILENAME} and Firebase.")
    print("Press 's' to stop logging.")

    threading.Thread(target=keypress_listener, daemon=True).start()

    # All possible fieldnames from all frames
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

    with open(CSV_FILENAME, 'w', newline='') as csvfile:
        writer = csv.DictWriter(csvfile, fieldnames=fieldnames, extrasaction='ignore')
        writer.writeheader()

        latest_data = {k: 0 for k in fieldnames}
        parsers = {
            EMU_IDS["FRAME_0"]: parse_emu_frame_0,
            EMU_IDS["FRAME_1"]: parse_emu_frame_1,
            EMU_IDS["FRAME_2"]: parse_emu_frame_2,
            EMU_IDS["FRAME_3"]: parse_emu_frame_3,
            EMU_IDS["FRAME_4"]: parse_emu_frame_4,
            EMU_IDS["FRAME_5"]: parse_emu_frame_5,
            EMU_IDS["FRAME_6"]: parse_emu_frame_6,
            EMU_IDS["FRAME_7"]: parse_emu_frame_7,
        }
        
        expected_ids = set(parsers.keys())
        received_ids_in_cycle = set()

        while not exit_flag:
            msg = bus.recv(timeout=0.2)
            if msg is None:
                continue

            parser = parsers.get(msg.arbitration_id)
            if not parser:
                continue

            parsed_values = parser(msg.data)
            if not parsed_values:
                continue
            
            # 데이터 타입 일관성을 위해 부동소수점 값 포맷팅
            for key, value in parsed_values.items():
                if isinstance(value, float):
                    parsed_values[key] = float(f"{value:.3f}")

            latest_data.update(parsed_values)
            received_ids_in_cycle.add(msg.arbitration_id)

            # When the base frame (0x600) is received and all other frames have been seen once
            if msg.arbitration_id == EMU_IDS["FRAME_0"] and received_ids_in_cycle.issuperset(expected_ids):
                ts = datetime.now().strftime("%Y-%m-%d %H:%M:%S.%f")[:-3]
                latest_data["Timestamp"] = ts
                
                # 1. Write to CSV
                writer.writerow(latest_data)

                # 2. Upload to Firebase
                try:
                    db_ref.update(latest_data)
                except Exception as e:
                    print(f"\nFirebase upload failed: {e}", file=sys.stderr)


                # 3. Print to CLI
                print(
                    f"[{ts}] "
                    f"RPM:{latest_data.get('RPM', 0):>5} | "
                    f"MAP:{latest_data.get('MAP_kPa', 0):>3}kPa | "
                    f"TPS:{latest_data.get('TPS_percent', 0):>5.1f}% | "
                    f"CLT:{latest_data.get('CLT_C', 0):>4}°C | "
                    f"Speed:{latest_data.get('VSS_kmh', 0):>3}km/h | "
                    f"Gear:{latest_data.get('Gear', 0):>2} | "
                    f"Batt:{latest_data.get('Batt_V', 0):>5.2f}V",
                    end="\r",
                    flush=True
                )
                
                received_ids_in_cycle.clear()

    bus.shutdown()
    print(" " * 120, end="\r") # Clear the line
    print(f"\nLogging stopped. File saved: {CSV_FILENAME}")

if __name__ == "__main__":
    main()


