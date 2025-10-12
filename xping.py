"""
# Stable CLI ping dashboard: RTT, jitter, loss, rolling AVG, left-anchored numeric history.
# ASCII only. Cross-platform. On Windows: pip install windows-curses

# Copyright (c) 2025 Charles Culver
# [GitHub](https://github.com/cculver78) • [Bluesky](https://bsky.app/profile/dhelmet78.bsky.social) • [Threads](https://www.threads.com/@cculver78)
# Licensed under the MIT License. See LICENSE file for details.
"""

import asyncio, json, sys, platform, re, shutil, argparse, curses, math
from dataclasses import dataclass, field
from typing import Optional, List, Dict

try:
    import winsound
except Exception:
    winsound = None

VERSION = "1.0.0"

DEFAULT_HOSTS = ["1.1.1.1", "8.8.8.8", "github.com", "google.com", "amazon.com", "facebook.com"]


@dataclass
class Host:
    name: str
    rtt: Optional[float] = None
    jitter: float = 0.0
    loss_pct: float = 0.0
    loss_window: List[int] = field(default_factory=list)  # 0 ok, 1 loss
    history: List[Optional[float]] = field(default_factory=list)


def parse_args():
    p = argparse.ArgumentParser(description=f"xPing {VERSION} — ASCII ping dashboard (CLI)")
    p.add_argument("--hosts", nargs="+", default=DEFAULT_HOSTS, help="Hosts to ping")
    p.add_argument("--interval", type=float, default=1.0, help="Ping interval seconds")
    p.add_argument("--loss-window", type=int, default=30, help="Window for loss calculation")
    p.add_argument("--hist-size", type=int, default=40, help="History length")
    p.add_argument("--timeout-ms", type=int, default=1000, help="Ping timeout in ms")
    p.add_argument("--sort", choices=["name", "rtt", "loss", "jitter"], default="name", help="Sort rows by this field")
    p.add_argument("--descending", action="store_true", help="Sort descending")
    p.add_argument("--json", action="store_true", help="Stream JSON lines to stdout (no curses UI)")
    p.add_argument("--beep", action="store_true", help="Enable beep on successful replies")
    p.add_argument("--version", action="version", version=f"%(prog)s {VERSION}")
    return p.parse_args()


def alert(stdscr, mode: str):
    """Cross-platform beep/tty BEL."""
    if mode == "tty":
        try:
            sys.stdout.write("\a"); sys.stdout.flush()
        except Exception:
            pass
        return
    try:
        curses.beep(); return
    except Exception:
        pass
    if platform.system().lower().startswith("win") and winsound:
        try:
            winsound.MessageBeep(-1); return
        except Exception:
            try:
                winsound.Beep(880, 120); return
            except Exception:
                pass
    try:
        sys.stdout.write("\a"); sys.stdout.flush()
    except Exception:
        pass


def ping_cmd(host: str, timeout_ms: int):
    sysname = platform.system().lower()
    if sysname.startswith("win"):
        # -n 1 one echo; -w timeout ms
        return (["ping", "-n", "1", "-w", str(timeout_ms), host],
                re.compile(r"time[=<]\s*(\d+)\s*ms|Average = (\d+)\s*ms", re.I))
    elif sysname == "darwin":
        # -c 1 one echo; -W timeout ms (mac accepts ms)
        return (["ping", "-c", "1", "-W", str(timeout_ms), host],
                re.compile(r"time[=<]\s*([\d\.]+)\s*ms", re.I))
    else:
        # Linux: -c 1; -W timeout sec (ceil from ms)
        tout = max(1, math.ceil(timeout_ms / 1000))
        return (["ping", "-c", "1", "-W", str(tout), host],
                re.compile(r"time[=<]\s*([\d\.]+)\s*ms", re.I))


async def ping_once(host: str, timeout_ms: int) -> Optional[float]:
    if not shutil.which("ping"):
        return None
    cmd, rx = ping_cmd(host, timeout_ms)
    try:
        proc = await asyncio.create_subprocess_exec(
            *cmd, stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE
        )
        stdout, _ = await asyncio.wait_for(proc.communicate(), timeout=(timeout_ms / 1000) + 1.5)
        m = rx.search(stdout.decode(errors="ignore"))
        if not m:
            return None
        groups = [g for g in m.groups() if g]
        if not groups:
            return None
        return float(groups[0])
    except Exception:
        return None


async def pinger(hosts: Dict[str, Host], interval: float, loss_win: int, timeout_ms: int, hist_size: int):
    while True:
        tasks = [(name, asyncio.create_task(ping_once(name, timeout_ms))) for name in hosts.keys()]
        for name, task in tasks:
            rtt = await task
            h = hosts[name]

            # update loss window
            h.loss_window.append(1 if rtt is None else 0)
            if len(h.loss_window) > loss_win:
                h.loss_window = h.loss_window[-loss_win:]
            h.loss_pct = 100.0 * (sum(h.loss_window) / max(1, len(h.loss_window)))

            # jitter EWMA
            if rtt is not None:
                if h.rtt is not None:
                    delta = abs(rtt - h.rtt)
                    h.jitter = 0.7 * h.jitter + 0.3 * delta
                else:
                    h.jitter = 0.0
                h.rtt = rtt

            # history ring
            h.history.append(rtt)
            if len(h.history) > hist_size:
                h.history = h.history[-hist_size:]

        await asyncio.sleep(interval)


def sort_hosts(hosts: List[Host], key: str, desc: bool) -> List[Host]:
    if key == "name":
        return sorted(hosts, key=lambda h: h.name.lower(), reverse=desc)
    if key == "rtt":
        return sorted(hosts, key=lambda h: (float("inf") if h.rtt is None else h.rtt), reverse=desc)
    if key == "loss":
        return sorted(hosts, key=lambda h: h.loss_pct, reverse=desc)
    if key == "jitter":
        return sorted(hosts, key=lambda h: h.jitter, reverse=desc)
    return hosts

def avg_ms(history: List[Optional[float]]) -> Optional[int]:
    vals = [v for v in history if v is not None]
    if not vals:
        return None
    return int(round(sum(vals) / len(vals)))

def draw_table(stdscr, rows: list, bell_mode: str):
    stdscr.erase()
    maxy, maxx = stdscr.getmaxyx()

    header = "xPing Table - CLI ping dashboard"
    stdscr.addstr(0, max(0, (maxx - len(header)) // 2), header[:maxx - 1])

    # Column widths
    name_w = max(12, min(24, maxx // 5))
    rtt_w  = 7
    jit_w  = 7
    loss_w = 6
    avg_w  = 7  # AVG column width
    fixed = 2 + name_w + rtt_w + jit_w + loss_w + avg_w  # separators included
    avail = max(0, maxx - fixed - 2)
    slots = max(4, avail // 4)  # "NNN " per sample

    def line(y: int, text: str):
        if 0 <= y < maxy:
            stdscr.addstr(y, 0, text[:maxx - 1])

    # Header row
    line(
        2,
        f"{'NAME'.ljust(name_w)} | "
        f"{'RTT'.rjust(rtt_w)} | "
        f"{'JITTER'.rjust(jit_w)} | "
        f"{'LOSS'.rjust(loss_w)} | "
        f"{'AVG'.rjust(avg_w)} | "
        f"HISTORY (newest→oldest)"
    )

    # Helper: rolling average over visible history buffer (ignores timeouts)
    def avg_ms(history: List[Optional[float]]) -> str:
        vals = [v for v in history if v is not None]
        if not vals:
            return "--"
        return str(int(round(sum(vals) / len(vals))))

    y = 3
    for h in rows:
        if y >= maxy - 1:
            break

        name = h.name[:name_w].ljust(name_w)
        rtt  = ("--" if h.rtt is None else f"{int(round(h.rtt))}").rjust(rtt_w)
        jit  = f"{int(round(h.jitter))}".rjust(jit_w)
        loss = f"{int(round(h.loss_pct))}%".rjust(loss_w)
        avg  = avg_ms(h.history).rjust(avg_w)

        # Left-anchored numeric ticker: newest on LEFT
        hist_vals = list(reversed(h.history[-slots:]))  # newest first
        hist_vals += [None] * (slots - len(hist_vals))  # pad right
        tokens = [("---" if v is None else f"{int(round(v)):>3}") for v in hist_vals]
        hist = " ".join(tokens)

        line(y, f"{name} | {rtt} | {jit} | {loss} | {avg} | {hist}")
        y += 1

    legend = f"Legend: --- no reply, newest on LEFT, b=beep({'ON' if bell_mode == 'on' else 'OFF'}), q=quit"
    line(maxy - 1, legend)
    stdscr.refresh()


async def ui_loop(stdscr, hosts: Dict[str, Host], args):
    curses.curs_set(0)
    stdscr.nodelay(True)

    # Start pinger
    asyncio.create_task(pinger(hosts, args.interval, args.loss_window, args.timeout_ms, args.hist_size))

    beep_enabled = bool(getattr(args, "beep", False))
    last_seen_len: Dict[str, int] = {name: 0 for name in hosts.keys()}

    while True:
        order = sort_hosts(list(hosts.values()), args.sort, args.descending)

        # Beep on every successful reply
        if beep_enabled:
            for h in order:
                cur_len = len(h.history)
                if cur_len > last_seen_len.get(h.name, 0):
                    if h.history[-1] is not None:
                        alert(stdscr, "beep")
                    last_seen_len[h.name] = cur_len

        draw_table(stdscr, order, "on" if beep_enabled else "off")

        try:
            ch = stdscr.getch()
            if ch in (ord("q"), ord("Q")):
                break
            if ch in (ord("b"), ord("B")):
                beep_enabled = not beep_enabled
            if ch == ord("B"):
                alert(stdscr, "beep")  # manual test
        except curses.error:
            pass

        await asyncio.sleep(0.05)

async def ui_json(hosts: Dict[str, Host], args):
    # Start pinger
    asyncio.create_task(pinger(hosts, args.interval, args.loss_window, args.timeout_ms, args.hist_size))
    # Stream batch JSON lines once per UI refresh (~20 fps might be overkill; use interval/2 min 0.2s)
    refresh = max(0.2, min(args.interval, 1.0))
    while True:
        snapshot = []
        for h in sort_hosts(list(hosts.values()), args.sort, args.descending):
            # Prepare a compact history tail (same slots that draw_table uses)
            # Derive console width if possible; for JSON just send the whole history buffer
            snapshot.append({
                "name": h.name,
                "rtt": None if h.rtt is None else int(round(h.rtt)),
                "jitter": int(round(h.jitter)),
                "loss_pct": int(round(h.loss_pct)),
                "avg": avg_ms(h.history),
                "history": [None if v is None else int(round(v)) for v in h.history],
            })
        sys.stdout.write(json.dumps({"type": "snapshot", "hosts": snapshot}) + "\n")
        sys.stdout.flush()
        await asyncio.sleep(refresh)

def main():
    args = parse_args()
    hosts: Dict[str, Host] = {h: Host(h) for h in args.hosts}
    if args.json:
        # Headless JSON mode for GUI frontends
        asyncio.run(ui_json(hosts, args))
        return
    def _wrap(scr):
        return asyncio.run(ui_loop(scr, hosts, args))
    curses.wrapper(_wrap)


if __name__ == "__main__":
    main()