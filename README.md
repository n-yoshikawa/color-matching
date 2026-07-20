# Color-Matching Digital Twin and Real Laboratory MCP Servers

This project demonstrates how an agent can develop a laboratory protocol against a deterministic digital twin and then run the same sequence of atomic MCP tools against real hardware.

The MCP surface intentionally exposes only atomic operations. Higher-level operations such as `wash_tip`, `add_color`, and `run_experiment` are composed by the client or agent from the shared atomic tool contract. For compatibility, `real/server.py` still retains its original higher-level methods and direct hardware sequences, but does not register them as MCP tools.

## Server implementations

### Digital twin: `mock/server.py`

The Mock server models:

- the robot's semantic location;
- six color source wells;
- twelve destination wells;
- the pipette's volume and liquid composition;
- source depletion and destination-well capacity;
- proportional aspiration from mixed liquids;
- a deterministic RGB mixing model;
- CIEDE2000 color difference;
- a successful-action trace.

The twin is designed to test protocol ordering, liquid conservation, capacities, and invalid operations. It does not simulate motion dynamics, serial timing, camera optics, or hardware failures.

### Real hardware server: `real/server.py`

The Real server implements the same atomic tool names and parameters using:

- a Dobot Magician robot arm;
- a Picus wired pipette;
- an OpenCV camera;
- camera-based well sampling and CIEDE2000 evaluation.

The Real server keeps only a small **commanded-state mirror** containing the semantic location and pipette volume. The physical commands remain directly inside `real/server.py`: robot movement uses `self.dobot`, pipetting uses `self.picus`, and color measurement uses the OpenCV camera. The mirror is not independent sensor confirmation; a partial hardware failure may require manual inspection before continuing.

## Shared MCP tool contract

Both servers expose:

| Tool | Purpose |
| --- | --- |
| `initialize()` | Move to home without resetting experiment state. |
| `return_home()` | Move to the safe home position. |
| `move_color_well(well)` | Move above source well `0..5`. |
| `move_mix_well(well)` | Move above destination well `0..11`. |
| `move_wash_station()` | Move to the semantic wash location. |
| `aspirate(volume_ul)` | Aspirate at the current liquid-containing location. |
| `dispense(volume_ul)` | Dispense the entire pipette volume and blow out. |
| `mix_well(volume_ul, cycles)` | Mix the current destination well. |
| `wash_tip(volume_ul, cycles)` | Move to the wash station and wash the empty tip. |
| `get_state()` | Return the portable location and pipette-volume state. |
| `get_labware_config()` | Return units, capacities, and well mappings. |
| `get_color_diff(well, hex)` | Return CIEDE2000 distance from a target color. |

Only the Mock server exposes `reset_simulation()` and `get_simulation_state()`. The former restores initial source volumes, empties the pipette and destination wells, returns to home, and restarts the trace. The latter returns detailed simulated pipette and well contents. There are deliberately no equivalent operations for physical hardware.

All volumes use microliters (`uL`):

- pipette capacity: `1000 uL`;
- destination-well capacity: `3000 uL`;
- initial usable volume per source well: `6000 uL`.

Source-well mapping:

| Well | Liquid |
| ---: | --- |
| 0, 3 | Blue |
| 1, 4 | Yellow |
| 2, 5 | Red |

Both servers load this mapping from `config/color_wells.json`. Changing that file updates the Mock and Real source-well identities together.

`dispense(volume_ul)` always includes blow out and must empty the pipette. Partial dispensing is rejected. Failed operations raise `ToolError`; callers must stop the protocol instead of continuing after an error.

## Known Mock/Real alignment gaps

The public tool names and input parameters are aligned, apart from the Mock-only `reset_simulation()` and `get_simulation_state()`. The following behavioral differences remain open design decisions:

- **Liquid and capacity enforcement:** Mock tracks source depletion, rejects aspiration from an empty well, and enforces the `3000 uL` destination capacity. Real does not have sensors for these values and currently does not enforce them. Source-well usable capacity is also not included in `get_labware_config()`.
- **Color evaluation:** Mock predicts RGB from a deterministic mixing model. Real samples the camera, so CIEDE2000 results are expected to differ. This is acceptable however given digital twin.

Portable protocols should use `get_state()` and must not depend on the Mock-only detailed simulation state.

### State responses

For the same commanded action sequence, both servers return the same minimal `get_state()` structure:

```json
{
  "location": {"kind": "mix_well", "index": 3},
  "pipette_volume_ul": 0
}
```

Mock's `get_simulation_state()` returns additional simulated liquid state. The example is abbreviated because the actual response includes all six source wells and all twelve mix wells:

```jsonc
{
  "location": {"kind": "mix_well", "index": 3},
  "pipette": {
    "volume_ul": 0,
    "contents": {},
    "capacity_ul": 1000
  },
  "color_wells": {
    "2": {
      "volume_ul": 5800,
      "contents": {"red": 5800}
    }
    // Other source wells omitted.
  },
  "mix_wells": {
    "3": {
      "volume_ul": 200,
      "contents": {"red": 200},
      "capacity_ul": 3000
    }
    // Other mix wells omitted.
  }
}
```

The Real response does not confirm that physical aspiration or dispensing succeeded; it only reports the state implied by successfully completed commands.

## Installation

Install the locked Python environment:

```bash
uv sync
```

The Real server additionally requires the Dobot vendor SDK, including `DobotDllType.py` and its native library. Update the SDK path and device port in `real/magician.py` for the machine controlling the robot. Also verify the Picus serial number and OpenCV camera index in `real/server.py`.

## Running the digital twin

```bash
uv run mock/server.py
```

The HTTP MCP endpoint is:

```text
http://127.0.0.1:8001/mcp
```

Run the example client in another terminal:

```bash
uv run client.py
```

The client demonstrates a reusable protocol assembled outside the server from atomic tool calls.

## Running real hardware

After configuring and connecting the Dobot, Picus, and camera:

```bash
uv run real/server.py
```

The endpoint and shared action signatures are the same as the Mock endpoint. Portable protocols can switch endpoints without changing their action calls. Do not run the Mock and Real servers on the same port at the same time.

Before a physical run:

1. confirm calibration coordinates and Z heights;
2. confirm source-well identity and available volume;
3. start with an empty destination plate;
4. inspect the commanded state and physical setup after any device error;
5. keep an operator ready to stop the hardware.

## Concurrency model

Each server currently owns one mutable laboratory state. State-changing tools must be called sequentially by one controller. Do not execute multiple protocols or parallel tool calls against the same server instance: their movement and liquid steps could interleave. Parallel simulation would require a separate `DigitalTwin` instance per simulation ID.

## Tests

Run the complete suite:

```bash
uv run --frozen pytest -q
```

The automated tests exercise the digital twin and client-side protocol assumptions. They do not instantiate `MySDL` or send commands to physical hardware.
