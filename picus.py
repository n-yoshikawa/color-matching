import serial
import serial.tools.list_ports

import time

class PicusWired:
    def __init__(self, serial_number: str, debug=False):
        self.serial_number = serial_number
        self.debug = debug
        self.max_wait_s = 10
        self.no = 0
        self.ser = None

        # --- connect ---
        for p in serial.tools.list_ports.comports():
            if p.serial_number == serial_number:
                try:
                    self.ser = serial.Serial(port=p.device, timeout=3)
                    if self.debug:
                        print(f"connected to Picus-{serial_number}")
                except serial.SerialException as e:
                    raise RuntimeError(f"failed to open serial port: {e}") from e
                break

        if self.ser is None:
            raise RuntimeError(f"no device Picus-{serial_number} found")

        self._send_command("VERBOSE 1")
        self._send_command("AUTO 1")
        self._send_command("ENABLE_MOTOR_CONTROL 1")

    def _send_payload(self, payload: str) -> None:
        n = self.no

        if self.debug:
            print(payload)
        self.ser.write(payload.encode())

        deadline = time.time() + self.max_wait_s

        # 0=ACK待ち, 1=BEGIN待ち, 2=BEGIN-END間
        state = 0
        ok_seen = False
        last_msg = ""

        while time.time() < deadline:
            line = self.ser.readline().decode(errors="ignore").strip()
            if self.debug:
                print("recv:", line)
            if not line:
                continue

            u = line.upper()

            if state == 0:
                if f"ACK {n}" in u:
                    state = 1
                continue

            if state == 1:
                if f"BEGIN {n}" in u:
                    state = 2
                continue

            # state == 2
            if "OK" in u:
                ok_seen = True
                continue

            if f"END {n}" in u:
                if ok_seen:
                    self.no += 1
                    return
                raise RuntimeError(f"Device error: \"{last_msg or 'No OK received'}")

            last_msg = line

        raise TimeoutError(f"Timeout (no={n}, state={state})")

    def _send_command(self, data: str) -> None:
        payload = f'{{"no": {self.no}, "data": "{data}"}}\r\n'
        self._send_payload(payload)

    def _push_button(self, button: str) -> None:
        payload = f'{{"no": {self.no}, "button": "{button}"}}\r\n'
        self._send_payload(payload)

    def disconnect(self) -> None:
        if self.ser and self.ser.is_open:
            try:
                for cmd in ("ENABLE_MOTOR_CONTROL 0", "AUTO 0"):
                    try:
                        self._send_command(cmd)
                    except Exception:
                        pass
            finally:
                self.ser.close()

            if self.debug:
                print(f"disconnected from Picus-{self.serial_number}")

    def __enter__(self):
        return self

    def __exit__(self, exc_type, exc, tb):
        self.disconnect()
        return False

    def __del__(self):
        try:
            self.disconnect()
        except Exception:
            pass

    def aspirate(self, amount, speed=5) -> None:
        self._send_command(f"RUN_ASPIRATE {amount} {speed}")

    def dispense(self, amount, speed=5) -> None:
        self._send_command(f"RUN_DISPENSE {amount} {speed}")

    def eject_tip(self) -> None:
        self._send_command("TIP_EJECT")

    def blow_out(self, go_home=1, speed=7, delay_ms=0) -> None:
        self._send_command(f"BLOW_OUT {go_home} {speed} {delay_ms}")

    def home(self) -> None:
        self._send_command("HOME")

    def press_button_right(self) -> None:
        self._push_button("TRIGGER_BUTTON_RIGHT")

if __name__ == '__main__':
    import time
    with PicusWired("47381939") as p:
        p.aspirate(100)
        for _ in range(5):
            p.dispense(20)
            time.sleep(1)

    # picus_1000ul = PicusWired("46781074")
    # picus_1000ul.button_top()
    # time.sleep(3)
    # picus_1000ul.button_top()