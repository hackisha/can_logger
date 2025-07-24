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

# === CAN 메시지 ID (EMUcan.h 기준) ===
EMU_ID_BASE      = 0x600  # RPM, TPS, IAT, MAP
EMU_ID_VSS_CLT   = 0x602  # Speed (VSS), CLT
EMU_ID_GEAR_BATT = 0x604  # Gear, Battery Voltage

# === 로그 디렉터리 및 파일 이름 설정 ===
LOG_DIR = "./logs"
os.makedirs(LOG_DIR, exist_ok=True)
CSV_FILENAME = f"{LOG_DIR}/emu_log_{datetime.now().strftime('%Y%m%d_%H%M%S')}.csv"

exit_flag = False

# === 파서 함수들 ===
def parse_emu_600(data):
    if len(data) != 8:
        return {}
    return {
        "RPM": struct.unpack_from('<H', data, 0)[0],
        "TPS_percent": data[2] * 0.5,
        "IAT_C": struct.unpack_from('b', data, 3)[0],
        "MAP_kPa": struct.unpack_from('<H', data, 4)[0],
    }

def parse_emu_602(data):
    if len(data) != 8:
        return {}
    speed = struct.unpack_from('<H', data, 0)[0]
    clt_c = int(struct.unpack_from('<h', data, 6)[0])
    return {"Speed_kmh": speed, "CLT_C": clt_c}

def parse_emu_604(data):
    if len(data) != 8:
        return {}
    gear = data[0]
    rawBatt = struct.unpack_from('<H', data, 2)[0]
    batt_v = round(rawBatt * 0.027, 2)
    return {"Gear": gear, "Batt_V": batt_v}

# === 's' 키로 종료 감지 스레드 ===
def keypress_listener():
    global exit_flag
    fd = sys.stdin.fileno()
    old = termios.tcgetattr(fd)
    try:
        tty.setcbreak(fd)
        while not exit_flag:
            if sys.stdin.read(1).lower() == 's':
                exit_flag = True
                break
            time.sleep(0.1)
    finally:
        termios.tcsetattr(fd, termios.TCSADRAIN, old)

# === 메인 로깅 루프 ===
def main():
    global exit_flag
    try:
        bus = can.interface.Bus(channel='can0', interface='socketcan')
    except Exception as e:
        print(f"CAN bus init error: {e}", file=sys.stderr)
        sys.exit(1)

    print(f"Logging to {CSV_FILENAME}  (press 's' to stop)")

    threading.Thread(target=keypress_listener, daemon=True).start()

    with open(CSV_FILENAME, 'w', newline='') as csvfile:
        fieldnames = [
            "Timestamp",
            "RPM", "TPS_percent", "IAT_C", "MAP_kPa",
            "Speed_kmh", "CLT_C",
            "Gear", "Batt_V"
        ]
        writer = csv.DictWriter(csvfile, fieldnames=fieldnames)
        writer.writeheader()

        latest = {k: 0 for k in fieldnames}
        parsers = {
            EMU_ID_BASE:      parse_emu_600,
            EMU_ID_VSS_CLT:   parse_emu_602,
            EMU_ID_GEAR_BATT: parse_emu_604,
        }
        expected_ids = set(parsers.keys())
        seen_ids = set()

        while not exit_flag:
            msg = bus.recv(timeout=0.2)
            if msg is None:
                continue

            parser = parsers.get(msg.arbitration_id)
            if not parser:
                continue

            parsed = parser(msg.data)
            if not parsed:
                continue

            latest.update(parsed)
            seen_ids.add(msg.arbitration_id)

            # 기준 프레임(0x600) 수신 시, 모든 expected ID를 받았으면 기록
            if msg.arbitration_id == EMU_ID_BASE and seen_ids.issuperset(expected_ids):
                ts = datetime.now().strftime("%Y-%m-%d %H:%M:%S.%f")[:-3]
                latest["Timestamp"] = ts
                writer.writerow(latest)

                # 한 줄 갱신 출력 (줄바꿈 없이)
                print(
                    f"[{ts}] "
                    f"RPM:{latest['RPM']:>5} | "
                    f"MAP:{latest['MAP_kPa']:>3}kPa | "
                    f"TPS:{latest['TPS_percent']:>5.1f}% | "
                    f"IAT:{latest['IAT_C']:>4}°C | "
                    f"Speed:{latest['Speed_kmh']:>3}km/h | "
                    f"CLT:{latest['CLT_C']:>4}°C | "
                    f"Gear:{latest['Gear']:>2} | "
                    f"Batt:{latest['Batt_V']:>5.2f}V",
                    end="\r",
                    flush=True
                )
                seen_ids.clear()

    bus.shutdown()
    # 마지막에 빈 칸으로 덮어쓰고 줄바꿈
    print(" " * 120, end="\r")
    print(f"\nLogging stopped. File saved: {CSV_FILENAME}")

if __name__ == "__main__":
    main()

