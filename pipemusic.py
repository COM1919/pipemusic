#!/usr/bin/env python3
"""
NBS 转红石音乐链 - 终极增强版 + 立体声支持 + TP玩家功能
- fill 优化提速
- 嵌套排版（圆/方）
- 3 音轨合并
- 乐器识别与自动分组
- 核心音轨组选择
- 实时进度 + RCON 重试 + 日志
- 音轨排序功能（按乐器归类，打击乐在后）
- 立体声效果：选择特定音轨，左右对称分布，实现双声道
- 单独命令方块链：生成时自动将指定玩家传送到中心轴上方2格
"""

import json
import math
import os
import sys
import time
from collections import defaultdict, Counter
from typing import List, Dict, Tuple, Set, Optional, Union

# 尝试导入 mcschematic（用于生成 .schem 文件）
MCSchematic_AVAILABLE = False
try:
    import mcschematic
    MCSchematic_AVAILABLE = True
except ImportError:
    print("[警告] 未安装 mcschematic 库，无法生成 .schem 文件。请运行 pip install mcschematic")
from pathlib import Path
from mcrcon import MCRcon
from pynbs import read as nbs_read
from pynbs import Layer

# ── 终端颜色 ─────────────────────────────────────────────────
class Ansi:
    RESET = "\033[0m"
    BOLD = "\033[1m"
    DIM = "\033[2m"
    RED = "\033[91m"
    GREEN = "\033[92m"
    YELLOW = "\033[93m"
    BLUE = "\033[94m"
    MAGENTA = "\033[95m"
    CYAN = "\033[96m"
    WHITE = "\033[97m"
    GRAY = "\033[90m"

    @staticmethod
    def colorize(text: str, color: str = "", bold: bool = False) -> str:
        return f"{Ansi.BOLD if bold else ''}{color}{text}{Ansi.RESET}"

    @staticmethod
    def title(text: str) -> str:
        return Ansi.colorize(text, Ansi.CYAN, bold=True)

    @staticmethod
    def success(text: str) -> str:
        return Ansi.colorize(f"✓ {text}", Ansi.GREEN)

    @staticmethod
    def error(text: str) -> str:
        return Ansi.colorize(f"✗ {text}", Ansi.RED)

    @staticmethod
    def prompt(text: str) -> str:
        return Ansi.colorize(text, Ansi.YELLOW)

    @staticmethod
    def info(text: str) -> str:
        return Ansi.colorize(text, Ansi.BLUE)

    @staticmethod
    def dim(text: str) -> str:
        return Ansi.colorize(text, Ansi.GRAY)


# ── 配置管理 ─────────────────────────────────────────────────
CONFIG_FILE = "nbs_chain_config.json"
DEFAULT_CONFIG = {
    "rcon_host": "127.0.0.1",
    "rcon_port": 25575,
    "rcon_password": "",
    "player_name": "COM1919",
    "direction": "east",
    "merge_groups": [],
    "layout_style": "flat",
    "layout_radius": 5,
    "master_group": None,
    "flat_spacing": 3,
    "nest_layers": 1,
    "layer_gap": 4,
    "num_threads": 4,
    "use_redstone_lamp": False,
    "stereo_layers": [],
    "uniform_repeater_mode": False,
    "tp_player": "",           # 新增：要自动传送的玩家名，为空则禁用
    "group_staircase_modes": {}  # 每组生成模式：{"0": "staircase", "1": "default", ...}
}

def load_config() -> dict:
    if os.path.exists(CONFIG_FILE):
        try:
            with open(CONFIG_FILE, 'r', encoding='utf-8') as f:
                cfg = json.load(f)
            for k, v in DEFAULT_CONFIG.items():
                cfg.setdefault(k, v)
            return cfg
        except:
            pass
    return dict(DEFAULT_CONFIG)

def save_config(cfg: dict):
    try:
        with open(CONFIG_FILE, 'w', encoding='utf-8') as f:
            json.dump(cfg, f, indent=2, ensure_ascii=False)
        print(Ansi.success("配置已保存"))
    except Exception as e:
        print(Ansi.error(f"保存配置失败: {e}"))


# ── 常量 ─────────────────────────────────────────────────────
INSTRUMENT_BLOCK_MAP: Dict[int, str] = {
    0: "minecraft:dirt",
    1: "minecraft:oak_planks",
    2: "minecraft:stone",
    3: "minecraft:sand",
    4: "minecraft:glass",
    5: "minecraft:white_wool",
    6: "minecraft:clay",
    7: "minecraft:gold_block",
    8: "minecraft:packed_ice",
    9: "minecraft:bone_block",
    10: "minecraft:iron_block",
    11: "minecraft:soul_sand",
    12: "minecraft:pumpkin",
    13: "minecraft:emerald_block",
    14: "minecraft:hay_block",
    15: "minecraft:glowstone"
}

INSTRUMENT_NAMES: Dict[int, str] = {
    0: "钢琴",
    1: "低音提琴",
    2: "底鼓",
    3: "小军鼓",
    4: "击鼓沿",
    5: "吉他",
    6: "长笛",
    7: "钟琴",
    8: "管钟",
    9: "木琴",
    10: "铁木琴",
    11: "牛铃",
    12: "迪吉里杜管",
    13: "芯片",
    14: "班卓琴",
    15: "电钢琴"
}

DRUM_INSTRUMENTS = {2, 3, 4}

DIR_OFFSET = {
    "east":  (1, 0, 0),
    "west":  (-1, 0, 0),
    "south": (0, 0, 1),
    "north": (0, 0, -1)
}
OPPOSITE_FACING = {
    "east": "west",
    "west": "east",
    "south": "north",
    "north": "south"
}
SIDE_AXIS = {
    "east":  "z",
    "west":  "z",
    "south": "x",
    "north": "x"
}

SUPPORT_BLOCK = "minecraft:white_wool"
CONNECTION_BLOCK = "minecraft:white_wool"
FALLING_BLOCK_SUPPORT = "minecraft:stone"


def note_to_pitch(key: int) -> int:
    return (key - 33) % 25

def get_note_key(note) -> int:
    return note.key if hasattr(note, 'key') else note.pitch

def split_to_repeaters(total_ticks: int) -> List[int]:
    if total_ticks <= 0:
        return []
    result = []
    remaining = total_ticks
    while remaining > 0:
        d = min(remaining, 4)
        result.append(d)
        remaining -= d
    return result


# ── 彩色公告 ─────────────────────────────────────────────────
def send_fancy_announce(mcr, target: str, song):
    def tellraw(msg_dict):
        cmd = f"tellraw {target} {json.dumps(msg_dict, ensure_ascii=False)}"
        mcr.command(cmd)

    GOLD = "gold"
    GREEN = "green"
    GRAY = "gray"
    DARK_GRAY = "dark_gray"
    WHITE = "white"
    YELLOW = "yellow"
    AQUA = "aqua"
    LIGHT_PURPLE = "light_purple"

    tellraw({"text": ""})
    tellraw([
        {"text": "▌ ", "color": GOLD, "bold": True},
        {"text": "NBS生成器 - PipeMusic", "color": GOLD, "bold": True},
        {"text": " ▌", "color": GOLD, "bold": True}
    ])
    tellraw({"text": ""})
    tellraw([
        {"text": "作者：", "color": GRAY},
        {"text": "COM1919", "color": AQUA},
        {"text": "    版本：", "color": GRAY},
        {"text": "v3.3 (立体声+阶梯模式)", "color": WHITE}
    ])
    tellraw([
        {"text": "B站首页：", "color": GRAY},
        {"text": "https://b23.tv/2POAj31", "color": LIGHT_PURPLE, "underlined": True,
         "clickEvent": {"action": "open_url", "value": "https://b23.tv/2POAj31"}}
    ])
    tellraw({"text": ""})
    tellraw([
        {"text": "─" * 30, "color": DARK_GRAY, "strikethrough": True}
    ])
    tellraw({"text": ""})
    song_name = song.header.song_name or "未知"
    author = song.header.original_author or "未知"
    description = song.header.description or "无"
    max_len = len(song.notes)
    max_layer = max(n.layer for n in song.notes) if song.notes else 0

    tellraw([
        {"text": "曲名：", "color": GRAY},
        {"text": song_name, "color": YELLOW, "bold": True}
    ])
    tellraw([
        {"text": "原始作者：", "color": GRAY},
        {"text": author, "color": GREEN}
    ])
    tellraw([
        {"text": "描述：", "color": GRAY},
        {"text": description, "color": WHITE}
    ])
    tellraw([
        {"text": "音符总数：", "color": GRAY},
        {"text": str(max_len), "color": WHITE}
    ])
    tellraw([
        {"text": "音轨数量：", "color": GRAY},
        {"text": str(max_layer + 1), "color": WHITE}
    ])
    tellraw({"text": ""})
    tellraw([
        {"text": "─" * 30, "color": DARK_GRAY, "strikethrough": True}
    ])
    tellraw({"text": ""})


# ── 乐器识别 ─────────────────────────────────────────────────
def get_instrument_category(inst_id: int) -> str:
    if inst_id in DRUM_INSTRUMENTS:
        return "鼓类"
    return INSTRUMENT_NAMES.get(inst_id, f"乐器{inst_id}")

def get_instrument_name(inst_id: int) -> str:
    return INSTRUMENT_NAMES.get(inst_id, f"未知({inst_id})")

def analyze_track_instrument(song, layer_id: int) -> Tuple[int, str]:
    notes = [n for n in song.notes if n.layer == layer_id]
    notes.sort(key=lambda n: n.tick)
    counter = defaultdict(int)
    sample_size = 30
    while sample_size <= 90 and not counter:
        window = notes[:sample_size]
        for note in window:
            counter[note.instrument] += 1
        if not counter and sample_size < len(notes):
            sample_size += 30
        else:
            break
    if not counter:
        return (0, "钢琴")
    main_inst = max(counter, key=counter.get)
    return (main_inst, get_instrument_category(main_inst))


# ── 音轨重排功能 ─────────────────────────────────────────────────
def get_instrument_group_key(inst_id: int) -> tuple:
    return (inst_id in DRUM_INSTRUMENTS, inst_id)

def rearrange_tracks_by_instrument(song):
    from collections import defaultdict

    notes_by_instrument = defaultdict(list)
    for note in song.notes:
        notes_by_instrument[note.instrument].append(note)

    new_layers = []
    instrument_layer_map = defaultdict(list)

    for inst_id, notes in sorted(notes_by_instrument.items(), key=lambda x: get_instrument_group_key(x[0])):
        notes.sort(key=lambda n: n.tick)
        layer_assignments = []
        for note in notes:
            tick = note.tick
            assigned = False
            for layer_notes in layer_assignments:
                if not layer_notes or layer_notes[-1][0] != tick:
                    layer_notes.append((tick, note))
                    assigned = True
                    break
            if not assigned:
                layer_assignments.append([(tick, note)])
        for _ in layer_assignments:
            instrument_layer_map[inst_id].append(len(new_layers))
            new_layers.append(inst_id)

    new_layer_objects = []
    for idx, inst_id in enumerate(new_layers):
        inst_name = INSTRUMENT_NAMES.get(inst_id, f"乐器{inst_id}")
        count = sum(1 for i in instrument_layer_map[inst_id] if i == idx)
        layer_name = f"{inst_name}_{count}" if count > 0 else inst_name
        new_layer_objects.append(Layer(idx, layer_name, 0, 0))
    song.layers = new_layer_objects

    for inst_id, layer_indices in instrument_layer_map.items():
        notes = notes_by_instrument[inst_id]
        notes.sort(key=lambda n: n.tick)
        layer_assignments = []
        new_id_for_layer = []
        for note in notes:
            tick = note.tick
            assigned = False
            for idx, layer_notes in enumerate(layer_assignments):
                if not layer_notes or layer_notes[-1][0] != tick:
                    layer_notes.append((tick, note))
                    assigned = True
                    break
            if not assigned:
                new_id = layer_indices[len(layer_assignments)]
                layer_assignments.append([(tick, note)])
                new_id_for_layer.append(new_id)
        for layer_notes, new_lid in zip(layer_assignments, new_id_for_layer):
            for (_, note) in layer_notes:
                note.layer = new_lid

    return song

def sort_groups_by_instrument(song, layer_to_group):
    group_instruments = {}
    for layer_id, group_id in layer_to_group.items():
        notes = [n for n in song.notes if n.layer == layer_id]
        if not notes:
            continue
        cnt = Counter(n.instrument for n in notes)
        main_inst = cnt.most_common(1)[0][0]
        group_instruments[group_id] = main_inst

    unique_groups = sorted(set(layer_to_group.values()), key=lambda gid: get_instrument_group_key(group_instruments[gid]))
    new_group_map = {old_gid: new_gid for new_gid, old_gid in enumerate(unique_groups)}
    new_layer_to_group = {lid: new_group_map[gid] for lid, gid in layer_to_group.items()}
    return new_layer_to_group


# ── 自动分组（基于虚拟层） ─────────────────────────────────
def auto_group_by_mode_on_layers(layers_info: List[Dict], mode: int) -> Dict[int, int]:
    """
    对虚拟层列表进行自动分组
    layers_info: 列表元素为 {'vid': int, 'original_id': int, 'side': str, 'inst': int, 'cat': str}
    返回 {vid: group_id}
    """
    if mode == 1:
        sorted_vids = sorted(layers_info, key=lambda x: (x['cat'], x['vid']))
    else:
        sorted_vids = sorted(layers_info, key=lambda x: x['vid'])
    layer_to_group = {}
    group_id = 0
    i = 0
    while i < len(sorted_vids):
        group = []
        if mode == 1:
            cat = sorted_vids[i]['cat']
            j = i
            while j < len(sorted_vids) and len(group) < 3 and sorted_vids[j]['cat'] == cat:
                group.append(sorted_vids[j]['vid'])
                j += 1
            i = j
        else:
            group = [item['vid'] for item in sorted_vids[i:i+3]]
            i += len(group)
        for vid in group:
            layer_to_group[vid] = group_id
        group_id += 1
    return layer_to_group


# ── 最小半径与嵌套辅助 ───────────────────────────────
def compute_min_radius(num_groups: int, style: str, has_master: bool = False) -> int:
    n = num_groups if not has_master else num_groups - 1
    if n <= 1:
        return 1
    if style in ("circle", "nest_circle"):
        return max(1, math.ceil(2 / math.sin(math.pi / n)))
    if style in ("square", "nest_square"):
        return max(1, math.ceil(n / 2))
    if style == "semicircle":
        return max(1, math.ceil(2 / math.sin(math.pi / (2*(n-1)))))
    if style == "semisquare":
        return max(1, math.ceil(n * 4 / 6))
    return 1

def calc_nest_layers(num_groups: int, max_radius: int, layer_gap: int = 4, min_spacing: int = 4) -> int:
    max_layers = 0
    r = max_radius
    while r >= 1:
        capacity = int(2 * math.pi * r / min_spacing)
        if capacity >= 1:
            max_layers += 1
            r -= layer_gap
        else:
            break
    return max(1, max_layers)

def distribute_groups_to_layers(num_groups: int, max_radius: int, num_layers: int,
                                layer_gap: int = 4, min_spacing: int = 4) -> List[Tuple[int, int]]:
    layers = []
    r = max_radius
    for _ in range(num_layers):
        capacity = int(2 * math.pi * r / min_spacing)
        layers.append([r, 0, capacity])
        r -= layer_gap
    remaining = num_groups
    for layer in layers:
        if remaining <= 0:
            break
        alloc = min(layer[2], remaining)
        layer[1] = alloc
        remaining -= alloc
    if remaining > 0:
        layers[-1][1] += remaining
    return [(r, cnt) for r, cnt, _ in layers]
# ── 最小半径与嵌套辅助 ───────────────────────────────
def compute_min_radius(num_groups: int, style: str, has_master: bool = False) -> int:
    n = num_groups if not has_master else num_groups - 1
    if n <= 1:
        return 1
    if style in ("circle", "nest_circle"):
        return max(1, math.ceil(2 / math.sin(math.pi / n)))
    if style in ("square", "nest_square"):
        return max(1, math.ceil(n / 2))
    if style == "semicircle":
        return max(1, math.ceil(2 / math.sin(math.pi / (2*(n-1)))))
    if style == "semisquare":
        return max(1, math.ceil(n * 4 / 6))
    return 1

def get_min_radius_for_nest_circle(num_groups: int, num_layers: int, layer_gap: int, min_spacing: int = 4) -> int:
    """计算能容纳 num_groups 个组、num_layers 层（圆形）所需的最小最大半径"""
    low, high = 1, max(1, num_groups * min_spacing // 2)
    best = high
    while low <= high:
        mid = (low + high) // 2
        r = mid
        remaining = num_groups
        for _ in range(num_layers):
            if r < 1:
                capacity = 0
            else:
                capacity = int(2 * math.pi * r / min_spacing)
            if capacity <= 0:
                break
            alloc = min(capacity, remaining)
            remaining -= alloc
            r -= layer_gap
        if remaining <= 0:
            best = mid
            high = mid - 1
        else:
            low = mid + 1
    return best

def get_min_radius_for_nest_square(num_groups: int, num_layers: int, layer_gap: int, min_spacing: int = 4) -> int:
    """计算能容纳 num_groups 个组、num_layers 层（方形）所需的最小最大半径"""
    low, high = 1, max(1, num_groups * min_spacing // 2)
    best = high
    while low <= high:
        mid = (low + high) // 2
        r = mid
        remaining = num_groups
        for _ in range(num_layers):
            if r < 1:
                capacity = 0
            else:
                capacity = int(8 * r / min_spacing)
            if capacity <= 0:
                break
            alloc = min(capacity, remaining)
            remaining -= alloc
            r -= layer_gap
        if remaining <= 0:
            best = mid
            high = mid - 1
        else:
            low = mid + 1
    return best

def distribute_groups_to_circle_layers(num_groups: int, max_radius: int, num_layers: int,
                                       layer_gap: int = 4, min_spacing: int = 4) -> List[Tuple[int, int]]:
    """
    圆形嵌套：将组数均匀分配到各层，确保每个有容量的层都能分到组（尽可能）。
    返回 [(半径, 该层组数), ...]，半径从大到小（外层先）。
    """
    # 计算各层最大容量
    layers = []
    r = max_radius
    for _ in range(num_layers):
        if r < 1:
            capacity = 0
        else:
            capacity = int(2 * math.pi * r / min_spacing)
        layers.append([r, 0, capacity])
        r -= layer_gap
    # 均匀分配：从内向外（反转），让每层获得近似相等的组数
    remaining = num_groups
    layers_rev = list(reversed(layers))
    ideal = num_groups // num_layers
    rem = num_groups % num_layers
    for i, layer in enumerate(layers_rev):
        target = ideal + (1 if i < rem else 0)
        alloc = min(target, layer[2])
        layer[1] = alloc
        remaining -= alloc
    # 若有剩余（容量不足），从外层开始补
    if remaining > 0:
        for layer in layers:
            if remaining <= 0:
                break
            avail = layer[2] - layer[1]
            if avail > 0:
                add = min(avail, remaining)
                layer[1] += add
                remaining -= add
    # 极端情况：全部塞给最大半径层
    if remaining > 0:
        layers[0][1] += remaining
    # 过滤空层
    result = [(r, cnt) for r, cnt, _ in layers if cnt > 0]
    if not result:
        result = [(layers[0][0], num_groups)]
    return result

def distribute_groups_to_square_layers(num_groups: int, max_radius: int, num_layers: int,
                                       layer_gap: int = 4, min_spacing: int = 4) -> List[Tuple[int, int]]:
    layers = []
    r = max_radius
    for _ in range(num_layers):
        if r < 1:
            capacity = 0
        else:
            capacity = int(8 * r / min_spacing)
        layers.append([r, 0, capacity])
        r -= layer_gap
    remaining = num_groups
    layers_rev = list(reversed(layers))
    ideal = num_groups // num_layers
    rem = num_groups % num_layers
    for i, layer in enumerate(layers_rev):
        target = ideal + (1 if i < rem else 0)
        alloc = min(target, layer[2])
        layer[1] = alloc
        remaining -= alloc
    if remaining > 0:
        for layer in layers:
            if remaining <= 0:
                break
            avail = layer[2] - layer[1]
            if avail > 0:
                add = min(avail, remaining)
                layer[1] += add
                remaining -= add
    if remaining > 0:
        layers[0][1] += remaining
    result = [(r, cnt) for r, cnt, _ in layers if cnt > 0]
    if not result:
        result = [(layers[0][0], num_groups)]
    return result

# ── 排版偏移计算（修复嵌套圆/方）────────────────────────────────
def compute_offsets(
    groups: List[int],
    config: dict,
    num_groups: int
) -> List[Tuple[int, int]]:
    style = config.get("layout_style", "flat")
    radius = config.get("layout_radius", 5)
    master = config.get("master_group")
    has_master = (master is not None and master < num_groups)

    pos_dict = {}

    if style == "flat":
        spacing = config.get("flat_spacing", 3)
        start = - (num_groups - 1) * spacing // 2
        for i, gid in enumerate(groups):
            pos_dict[gid] = (0, start + i * spacing)

    elif style == "nest_circle":
        max_radius = radius
        num_layers = config.get("nest_layers", 1)
        layer_gap = config.get("layer_gap", 4)
        remaining_groups = [g for g in groups if g != master] if has_master else groups[:]
        need_radius = get_min_radius_for_nest_circle(len(remaining_groups), num_layers, layer_gap, min_spacing=4)
        if max_radius < need_radius:
            print(Ansi.prompt(f"警告：当前半径 {max_radius} 不足以容纳 {len(remaining_groups)} 个组分成 {num_layers} 层，已自动调整为 {need_radius}"))
            max_radius = need_radius
            config["layout_radius"] = max_radius
        layers_info = distribute_groups_to_circle_layers(len(remaining_groups), max_radius, num_layers, layer_gap, min_spacing=4)
        if has_master:
            pos_dict[master] = (0, 0)
        idx = 0
        for r, cnt in layers_info:
            for i in range(cnt):
                angle = 2 * math.pi * i / cnt if cnt > 0 else 0
                y = round(r * math.cos(angle))
                side = round(r * math.sin(angle))
                gid = remaining_groups[idx]
                pos_dict[gid] = (y, side)
                idx += 1

    elif style == "nest_square":
        max_radius = radius
        num_layers = config.get("nest_layers", 1)
        layer_gap = config.get("layer_gap", 4)
        remaining_groups = [g for g in groups if g != master] if has_master else groups[:]
        need_radius = get_min_radius_for_nest_square(len(remaining_groups), num_layers, layer_gap, min_spacing=4)
        if max_radius < need_radius:
            print(Ansi.prompt(f"警告：当前半径 {max_radius} 不足以容纳 {len(remaining_groups)} 个组分成 {num_layers} 层，已自动调整为 {need_radius}"))
            max_radius = need_radius
            config["layout_radius"] = max_radius
        layers_info = distribute_groups_to_square_layers(len(remaining_groups), max_radius, num_layers, layer_gap, min_spacing=4)
        if has_master:
            pos_dict[master] = (0, 0)
        idx = 0
        for r, cnt in layers_info:
            per_side = max(1, (cnt + 3) // 4)
            points = []
            # 上边
            for i in range(per_side):
                x = -r + (2 * r) * i / (per_side - 1) if per_side > 1 else 0
                points.append((r, round(x)))
            # 右边
            for i in range(per_side):
                y = r - (2 * r) * i / (per_side - 1) if per_side > 1 else 0
                points.append((round(y), r))
            # 下边
            for i in range(per_side):
                x = r - (2 * r) * i / (per_side - 1) if per_side > 1 else 0
                points.append((-r, round(x)))
            # 左边
            for i in range(per_side):
                y = -r + (2 * r) * i / (per_side - 1) if per_side > 1 else 0
                points.append((round(y), -r))
            unique = []
            seen = set()
            for pt in points:
                if pt not in seen:
                    seen.add(pt)
                    unique.append(pt)
            unique = unique[:cnt]
            for i in range(cnt):
                y, side = unique[i]
                gid = remaining_groups[idx]
                pos_dict[gid] = (y, side)
                idx += 1

    elif style == "circle":
        if has_master and master in groups:
            pos_dict[master] = (0, 0)
            other = [g for g in groups if g != master]
            n = len(other)
            for i, gid in enumerate(other):
                angle = 2 * math.pi * i / n
                y = round(radius * math.cos(angle))
                side = round(radius * math.sin(angle))
                pos_dict[gid] = (y, side)
        else:
            for i, gid in enumerate(groups):
                angle = 2 * math.pi * i / len(groups)
                y = round(radius * math.cos(angle))
                side = round(radius * math.sin(angle))
                pos_dict[gid] = (y, side)

    elif style == "square":
        n = len(groups) - (1 if has_master else 0)
        if n <= 0:
            for gid in groups:
                pos_dict[gid] = (0, 0)
        else:
            side_len = 2 * radius
            per_side = max(1, int(math.ceil(n / 4)))
            top = [(radius, -radius + i * side_len // (per_side - 1)) for i in range(per_side)] if per_side > 1 else [(radius,0)]
            right = [(-radius + i * side_len // (per_side - 1), radius) for i in range(per_side)] if per_side > 1 else [(0,radius)]
            bottom = [(-radius, radius - i * side_len // (per_side - 1)) for i in range(per_side)] if per_side > 1 else [(-radius,0)]
            left = [(radius - i * side_len // (per_side - 1), -radius) for i in range(per_side)] if per_side > 1 else [(0,-radius)]
            points = top + right + bottom + left
            points = points[:n]
            if has_master:
                pos_dict[master] = (0, 0)
                idx = 0
                for gid in groups:
                    if gid == master:
                        continue
                    pos_dict[gid] = points[idx]
                    idx += 1
            else:
                for i, gid in enumerate(groups):
                    pos_dict[gid] = points[i]

    elif style == "semicircle":
        n = len(groups) - (1 if has_master else 0)
        if n <= 0:
            for gid in groups:
                pos_dict[gid] = (0, 0)
        else:
            angles = [math.pi * i / (n - 1) for i in range(n)] if n > 1 else [0]
            point_list = [(round(radius * math.cos(a)), round(radius * math.sin(a))) for a in angles]
            if has_master:
                pos_dict[master] = (0, 0)
                idx = 0
                for gid in groups:
                    if gid == master:
                        continue
                    pos_dict[gid] = point_list[idx]
                    idx += 1
            else:
                for i, gid in enumerate(groups):
                    pos_dict[gid] = point_list[i]

    elif style == "semisquare":
        n = len(groups) - (1 if has_master else 0)
        if n <= 0:
            for gid in groups:
                pos_dict[gid] = (0, 0)
        else:
            side_len = 2 * radius
            num_top = max(1, n // 2)
            num_side = max(1, (n - num_top) // 2)
            left_side = [(-radius, -radius + i * side_len // (num_side - 1)) for i in range(num_side)] if num_side > 1 else [(-radius,0)]
            top = [(-radius + i * side_len // (num_top - 1), radius) for i in range(num_top)] if num_top > 1 else [(0,radius)]
            right_side = [(radius, radius - i * side_len // (num_side - 1)) for i in range(num_side)] if num_side > 1 else [(radius,0)]
            points = left_side + top + right_side
            points = points[:n]
            if has_master:
                pos_dict[master] = (0, 0)
                idx = 0
                for gid in groups:
                    if gid == master:
                        continue
                    pos_dict[gid] = points[idx]
                    idx += 1
            else:
                for i, gid in enumerate(groups):
                    pos_dict[gid] = points[i]

    else:
        spacing = config.get("flat_spacing", 3)
        start = - (num_groups - 1) * spacing // 2
        for i, gid in enumerate(groups):
            pos_dict[gid] = (0, start + i * spacing)

    return [pos_dict[gid] for gid in groups]
# ── 命令生成（基于虚拟层和组映射）─────────────────────────────────
def generate_fill_commands(
    groups: List[int],
    group_offsets: List[Tuple[int, int]],
    base: Tuple[int, int, int],
    direction: str,
    max_tick: int,
    side_axis: str,
    use_lamp: bool = False
) -> List[str]:
    dx, dy, dz = DIR_OFFSET[direction]
    base_x, base_y, base_z = base
    fill_cmds = []
    for i, gid in enumerate(groups):
        y_off, side_off = group_offsets[i]
        if side_axis == 'z':
            line_z = base_z + side_off
            line_x0 = base_x
        else:
            line_z = base_z
            line_x0 = base_x + side_off

        start_x = line_x0
        end_x = start_x + dx * (max_tick + 2)
        if dx > 0:
            min_x, max_x = min(start_x, end_x), max(start_x, end_x)
        else:
            min_x, max_x = min(start_x, end_x), max(start_x, end_x)

        cur = min_x
        while cur <= max_x:
            seg_end = min(cur + abs(dx) * 31, max_x)
            x1, x2 = (cur, seg_end) if dx > 0 else (seg_end, cur)
            fill_cmds.append(
                f"fill {x1} {base_y + y_off} {line_z} {x2} {base_y + y_off} {line_z} minecraft:white_wool"
            )
            if use_lamp:
                fill_cmds.append(
                    f"fill {x1} {base_y + y_off} {line_z} {x2} {base_y + y_off} {line_z} minecraft:redstone_lamp"
                )
            cur = seg_end + abs(dx)
    return fill_cmds

def generate_schematic_from_commands(commands, output_path, base_name="redstone_music"):
    if not MCSchematic_AVAILABLE:
        print(Ansi.error("无法生成 .schem：缺少 mcschematic 库"))
        return False

    schem = mcschematic.MCSchematic()
    total_blocks = 0
    min_x = min_y = min_z = float('inf')
    max_x = max_y = max_z = float('-inf')

    def update_bounds(x, y, z):
        nonlocal min_x, min_y, min_z, max_x, max_y, max_z
        min_x = min(min_x, x)
        min_y = min(min_y, y)
        min_z = min(min_z, z)
        max_x = max(max_x, x)
        max_y = max(max_y, y)
        max_z = max(max_z, z)

    def set_block(x, y, z, block_str):
        schem.setBlock((x, y, z), block_str)
        update_bounds(x, y, z)

    import re
    setblock_pattern = re.compile(r'^setblock\s+(-?\d+)\s+(-?\d+)\s+(-?\d+)\s+(.+)')
    fill_pattern = re.compile(r'^fill\s+(-?\d+)\s+(-?\d+)\s+(-?\d+)\s+(-?\d+)\s+(-?\d+)\s+(-?\d+)\s+(.+)')
    
    for cmd in commands:
        cmd = cmd.strip()
        if cmd.startswith('#') or not cmd:
            continue

        m = setblock_pattern.match(cmd)
        if m:
            x, y, z = int(m.group(1)), int(m.group(2)), int(m.group(3))
            block = m.group(4)
            set_block(x, y, z, block)
            total_blocks += 1
            continue

        m = fill_pattern.match(cmd)
        if m:
            x1, y1, z1 = int(m.group(1)), int(m.group(2)), int(m.group(3))
            x2, y2, z2 = int(m.group(4)), int(m.group(5)), int(m.group(6))
            block = m.group(7)
            x_start, x_end = sorted([x1, x2])
            y_start, y_end = sorted([y1, y2])
            z_start, z_end = sorted([z1, z2])
            for x in range(x_start, x_end + 1):
                for y in range(y_start, y_end + 1):
                    for z in range(z_start, z_end + 1):
                        set_block(x, y, z, block)
                        total_blocks += 1
            continue

    if total_blocks == 0:
        print(Ansi.error("没有找到任何方块放置命令，无法生成 schematic"))
        return False

    offset_x, offset_y, offset_z = min_x, min_y, min_z
    print(Ansi.info(f"结构文件包含 {total_blocks} 个方块，范围: X{min_x}~{max_x}, Y{min_y}~{max_y}, Z{min_z}~{max_z}"))
    print(Ansi.dim(f"放置时建议使用 //schem load 后，以世界坐标 ({offset_x},{offset_y},{offset_z}) 为原点粘贴"))

    import os
    dir_name = os.path.dirname(output_path)
    base_name = os.path.basename(output_path)
    if base_name.endswith('.schem'):
        base_name = base_name[:-6]
    if not dir_name:
        dir_name = "."
    try:
        os.makedirs(dir_name, exist_ok=True)
        schem.save(dir_name, base_name, mcschematic.Version.JE_1_21)
        print(Ansi.success(f"结构文件已保存: {os.path.join(dir_name, base_name + '.schem')}"))
        return True
    except Exception as e:
        print(Ansi.error(f"保存 schematic 失败: {e}"))
        return False

def build_all_commands_virtual(
    virtual_layers: List[Dict],
    layer_to_group: Dict[int, int],
    group_offsets: List[Tuple[int, int]],
    base: Tuple[int, int, int],
    direction: str,
    max_tick: int,
    use_lamp: bool = False,
    uniform_repeater_mode: bool = False,   # 原 repeater_gap_fill
    group_staircase_modes: Dict[int, str] = None  # 每组生成模式
) -> Tuple[List[str], Dict[int, Tuple[int, int, int]]]:
    dx, dy, dz = DIR_OFFSET[direction]
    facing = OPPOSITE_FACING[direction]
    side_axis = SIDE_AXIS[direction]
    groups = sorted(set(layer_to_group.values()))
    if group_staircase_modes is None:
        group_staircase_modes = {}
    base_block = "minecraft:redstone_lamp" if use_lamp else "minecraft:white_wool"

    if direction in ("east", "west"):
        axis_step = dx
        axis_key = 'x'
    else:
        axis_step = dz
        axis_key = 'z'

    # 收集每个组在每个tick的音符
    group_tick_notes = defaultdict(lambda: defaultdict(list))
    for vlayer in virtual_layers:
        gid = layer_to_group[vlayer['vid']]
        for note in vlayer['notes']:
            if 0 <= note.tick <= max_tick:
                group_tick_notes[gid][note.tick].append(note)

    valid_ticks = set()
    for gid in groups:
        valid_ticks.update(group_tick_notes[gid].keys())
    valid_ticks = sorted(valid_ticks)
    if max_tick not in valid_ticks:
        valid_ticks.append(max_tick)

    base_x, base_y, base_z = base
    activation_positions = {}
    group_pos = {}
    fill_cursor = {}

    # 初始化各组参数
    for i, gid in enumerate(groups):
        y_off, side_off = group_offsets[i]
        if side_axis == 'z':
            x0 = base_x
            z0 = base_z + side_off
        else:
            x0 = base_x + side_off
            z0 = base_z
        y0 = base_y + y_off

        relay_x = x0 - dx
        relay_y = y0
        if side_axis == 'z':
            relay_z = z0 - dz
        else:
            relay_z = z0 - dz if direction in ("south", "north") else z0

        input_x = relay_x - dx
        input_y = relay_y
        input_z = relay_z - dz if direction in ("south", "north") else relay_z
        activation_positions[gid] = (input_x, input_y, input_z)

        group_pos[gid] = (relay_x, relay_y, relay_z)

        if axis_key == 'x':
            axis_origin = relay_x
        else:
            axis_origin = relay_z
        fill_cursor[gid] = axis_origin - 2 * axis_step

    all_cmds = ["# 红石音乐生成器 (中继器链 + 辅助方块)"]
    SEGMENT = 32

    def get_fixed_and_y(gid):
        i = groups.index(gid)
        y_off, side_off = group_offsets[i]
        y0 = base_y + y_off
        if axis_key == 'x':
            fixed = base_z + (side_off if side_axis == 'z' else 0)
        else:
            fixed = base_x + (side_off if side_axis == 'x' else 0)
        return fixed, y0

    def ensure_support(gid, target_coord):
        nonlocal fill_cursor
        cursor = fill_cursor[gid]
        if axis_step > 0:
            need = target_coord > cursor
        else:
            need = target_coord < cursor
        if not need:
            return
        cur = cursor + axis_step
        if axis_step > 0:
            seg_end = cur + SEGMENT - 1
        else:
            seg_end = cur - (SEGMENT - 1)
        fixed, y0 = get_fixed_and_y(gid)
        if axis_key == 'x':
            x1, x2 = (cur, seg_end) if cur <= seg_end else (seg_end, cur)
            all_cmds.append(f"fill {x1} {y0-1} {fixed} {x2} {y0} {fixed} {base_block}")
        else:
            z1, z2 = (cur, seg_end) if cur <= seg_end else (seg_end, cur)
            all_cmds.append(f"fill {fixed} {y0-1} {z1} {fixed} {y0} {z2} {base_block}")
        fill_cursor[gid] = seg_end

    # 放置起始中继器
    for gid in groups:
        x, y, z = group_pos[gid]
        target_coord = x if axis_key == 'x' else z
        ensure_support(gid, target_coord)
        all_cmds.append(f"setblock {x} {y} {z} minecraft:repeater[facing={facing},delay=1]")

    prev_valid_tick = None
    for valid_tick in valid_ticks:
        gap = 0 if prev_valid_tick is None else valid_tick - prev_valid_tick

        if gap > 0:
            if prev_valid_tick is None:
                blank_ticks = 0
            else:
                blank_ticks = valid_tick - prev_valid_tick - 1
            
            if blank_ticks < 0:
                blank_ticks = 0   # 安全保护
            
            if uniform_repeater_mode:
                delays = [1] * blank_ticks   # 每个空白 tick 一个 delay=1 中继器
            else:
                delays = split_to_repeaters(blank_ticks)

            for gid in groups:
                x, y, z = group_pos[gid]
                for idx, delay_val in enumerate(delays):
                    # 放置辅助方块（白色羊毛），将被当前中继器充能
                    connector_x = x + dx
                    connector_z = z + dz if side_axis == 'z' else z
                    ensure_support(gid, connector_x if axis_key == 'x' else connector_z)
                    all_cmds.append(f"setblock {connector_x} {y} {connector_z} minecraft:white_wool")

                    # 在辅助方块前方放置下一个中继器
                    next_repeater_x = connector_x + dx
                    next_repeater_z = connector_z + dz if side_axis == 'z' else connector_z
                    ensure_support(gid, next_repeater_x if axis_key == 'x' else next_repeater_z)
                    all_cmds.append(
                        f"setblock {next_repeater_x} {y} {next_repeater_z} "
                        f"minecraft:repeater[facing={facing},delay={delay_val}]"
                    )
                    # 更新位置
                    x, y, z = next_repeater_x, y, next_repeater_z
                group_pos[gid] = (x, y, z)

        # ---------- 当前 tick 的音符盒放置 ----------
        for gid in groups:
            x, y, z = group_pos[gid]
            note_x = x + dx
            note_z = z + dz if side_axis == 'z' else z
            target_coord = note_x if axis_key == 'x' else note_z
            ensure_support(gid, target_coord)

            notes = group_tick_notes[gid].get(valid_tick, [])
            note_count = len(notes)
            is_stair = group_staircase_modes.get(str(gid), "default") == "staircase"

            if note_count == 0:
                # 空 tick：放置 base_block（中继器直接充能该方块）
                if axis_key == 'x':
                    all_cmds.append(f"setblock {note_x} {y} {note_z} {base_block}")
                else:
                    all_cmds.append(f"setblock {note_x} {y} {note_z} {base_block}")
                # 然后放置后续中继器（延续信号）
                next_relay_x = note_x + dx
                next_relay_z = note_z + dz if side_axis == 'z' else note_z
                ensure_support(gid, next_relay_x if axis_key == 'x' else next_relay_z)
                all_cmds.append(f"setblock {next_relay_x} {y} {next_relay_z} minecraft:repeater[facing={facing},delay=1]")
                group_pos[gid] = (next_relay_x, y, next_relay_z)
            elif note_count == 1:
                note = notes[0]
                pitch = note_to_pitch(get_note_key(note))
                inst = note.instrument
                if axis_key == 'x':
                    all_cmds.append(f"setblock {note_x} {y} {note_z} minecraft:note_block[note={pitch}]")
                else:
                    all_cmds.append(f"setblock {note_x} {y} {note_z} minecraft:note_block[note={pitch}]")
                if inst != 0:
                    if axis_key == 'x':
                        all_cmds.append(f"setblock {note_x} {y-1} {note_z} {INSTRUMENT_BLOCK_MAP.get(inst, 'minecraft:stone')}")
                        if inst == 3:
                            all_cmds.append(f"setblock {note_x} {y-2} {note_z} {FALLING_BLOCK_SUPPORT}")
                    else:
                        all_cmds.append(f"setblock {note_x} {y-1} {note_z} {INSTRUMENT_BLOCK_MAP.get(inst, 'minecraft:stone')}")
                        if inst == 3:
                            all_cmds.append(f"setblock {note_x} {y-2} {note_z} {FALLING_BLOCK_SUPPORT}")
                # 放置后续中继器
                next_relay_x = note_x + dx
                next_relay_z = note_z + dz if side_axis == 'z' else note_z
                ensure_support(gid, next_relay_x if axis_key == 'x' else next_relay_z)
                all_cmds.append(f"setblock {next_relay_x} {y} {next_relay_z} minecraft:repeater[facing={facing},delay=1]")
                group_pos[gid] = (next_relay_x, y, next_relay_z)
            elif note_count == 2:
                side_step = 1
                side_y = y - 1 if is_stair else y  # 阶梯模式：侧边音符下降一格
                if side_axis == 'z':
                    left_pos = (note_x, side_y, note_z - side_step)
                    right_pos = (note_x, side_y, note_z + side_step)
                else:
                    left_pos = (note_x - side_step, side_y, note_z)
                    right_pos = (note_x + side_step, side_y, note_z)
                sorted_notes = sorted(notes, key=lambda n: n.layer)
                if axis_key == 'x':
                    all_cmds.append(f"setblock {note_x} {y} {note_z} {base_block}")
                else:
                    all_cmds.append(f"setblock {note_x} {y} {note_z} {base_block}")
                for pos, note in zip([left_pos, right_pos], sorted_notes):
                    px, py, pz = pos
                    pitch = note_to_pitch(get_note_key(note))
                    inst = note.instrument
                    all_cmds.append(f"setblock {px} {py} {pz} minecraft:note_block[note={pitch}]")
                    if inst != 0:
                        all_cmds.append(f"setblock {px} {py-1} {pz} {INSTRUMENT_BLOCK_MAP.get(inst, 'minecraft:stone')}")
                        if inst == 3:
                            all_cmds.append(f"setblock {px} {py-2} {pz} {FALLING_BLOCK_SUPPORT}")
                next_relay_x = note_x + dx
                next_relay_z = note_z + dz if side_axis == 'z' else note_z
                ensure_support(gid, next_relay_x if axis_key == 'x' else next_relay_z)
                all_cmds.append(f"setblock {next_relay_x} {y} {next_relay_z} minecraft:repeater[facing={facing},delay=1]")
                group_pos[gid] = (next_relay_x, y, next_relay_z)
            else:  # >=3
                sorted_notes = sorted(notes, key=lambda n: n.layer)
                mid_note = sorted_notes[1] if len(sorted_notes) > 1 else sorted_notes[0]
                left_note = sorted_notes[0]
                right_note = sorted_notes[-1]
                side_step = 1
                side_y = y - 1 if is_stair else y  # 阶梯模式：侧边音符下降一格
                if side_axis == 'z':
                    left_pos = (note_x, side_y, note_z - side_step)
                    right_pos = (note_x, side_y, note_z + side_step)
                else:
                    left_pos = (note_x - side_step, side_y, note_z)
                    right_pos = (note_x + side_step, side_y, note_z)
                pitch_mid = note_to_pitch(get_note_key(mid_note))
                inst_mid = mid_note.instrument
                all_cmds.append(f"setblock {note_x} {y} {note_z} minecraft:note_block[note={pitch_mid}]")
                if inst_mid != 0:
                    all_cmds.append(f"setblock {note_x} {y-1} {note_z} {INSTRUMENT_BLOCK_MAP.get(inst_mid, 'minecraft:stone')}")
                    if inst_mid == 3:
                        all_cmds.append(f"setblock {note_x} {y-2} {note_z} {FALLING_BLOCK_SUPPORT}")
                for pos, note in zip([left_pos, right_pos], [left_note, right_note]):
                    px, py, pz = pos
                    pitch = note_to_pitch(get_note_key(note))
                    inst = note.instrument
                    all_cmds.append(f"setblock {px} {py} {pz} minecraft:note_block[note={pitch}]")
                    if inst != 0:
                        all_cmds.append(f"setblock {px} {py-1} {pz} {INSTRUMENT_BLOCK_MAP.get(inst, 'minecraft:stone')}")
                        if inst == 3:
                            all_cmds.append(f"setblock {px} {py-2} {pz} {FALLING_BLOCK_SUPPORT}")
                next_relay_x = note_x + dx
                next_relay_z = note_z + dz if side_axis == 'z' else note_z
                ensure_support(gid, next_relay_x if axis_key == 'x' else next_relay_z)
                all_cmds.append(f"setblock {next_relay_x} {y} {next_relay_z} minecraft:repeater[facing={facing},delay=1]")
                group_pos[gid] = (next_relay_x, y, next_relay_z)

        prev_valid_tick = valid_tick

    return all_cmds, activation_positions

# ── 移除空音轨、垂直压缩（保持不变）───────────────────────────────
def remove_empty_layers(song):
    layers_with_notes = {note.layer for note in song.notes}
    if not layers_with_notes:
        song.layers = []
        return song
    id_map = {}
    new_layers = []
    new_id = 0
    for layer in song.layers:
        if layer.id in layers_with_notes:
            id_map[layer.id] = new_id
            new_layers.append(Layer(new_id, layer.name, layer.volume, layer.panning))
            new_id += 1
    for note in song.notes:
        note.layer = id_map[note.layer]
    song.layers = new_layers
    return song

def ensure_one_empty_layer(song):
    song = remove_empty_layers(song)

    layers_with_notes = {note.layer for note in song.notes}
    all_layers = set(range(len(song.layers)))
    empty_layers = all_layers - layers_with_notes

    if not empty_layers:
        new_id = len(song.layers)
        empty_layer = Layer(new_id, "空音轨", 0, 0)
        song.layers.append(empty_layer)
    else:
        keep_id = min(empty_layers)
        new_layers = []
        for idx, layer in enumerate(song.layers):
            if idx == keep_id or idx not in empty_layers:
                new_layers.append(layer)
        id_map = {}
        final_layers = []
        for new_id, layer in enumerate(new_layers):
            id_map[layer.id] = new_id
            final_layers.append(Layer(new_id, layer.name, layer.volume, layer.panning))
        for note in song.notes:
            note.layer = id_map[note.layer]
        song.layers = final_layers
    return song

def vertical_compress(song):
    from collections import defaultdict
    tick_notes = defaultdict(list)
    for note in song.notes:
        tick_notes[note.tick].append(note)

    max_new_layer = 0
    for tick, notes in tick_notes.items():
        notes_sorted = sorted(notes, key=lambda n: n.layer)
        for idx, note in enumerate(notes_sorted):
            note.layer = idx
            if idx > max_new_layer:
                max_new_layer = idx

    new_layer_notes = defaultdict(list)
    for note in song.notes:
        new_layer_notes[note.layer].append(note)

    new_layers = []
    for lid in range(max_new_layer + 1):
        notes_in_layer = new_layer_notes.get(lid, [])
        if notes_in_layer:
            inst = notes_in_layer[0].instrument
            inst_name = INSTRUMENT_NAMES.get(inst, f"乐器{inst}")
            layer_name = f"{inst_name}_{lid}" if lid > 0 else inst_name
        else:
            layer_name = f"空层_{lid}"
        new_layers.append(Layer(lid, layer_name, 100, 0))
    song.layers = new_layers
    return song


# ── RCON 发送（保持不变）───────────────────────────────────────
def format_time(seconds: int) -> str:
    if seconds < 0: seconds = 0
    minutes = seconds // 60
    secs = seconds % 60
    return f"{minutes}分{secs}秒" if minutes else f"{secs}秒"

def send_via_rcon(commands: List[str], host: str, port: int, password: str, player: str,
                  direction: str, song, start_x: int, start_y: int, start_z: int):
    total = len([c for c in commands if not c.startswith('#')])
    log_file = "nbs_failed_commands.txt"
    max_retries = 3

    def try_connect():
        mcr = MCRcon(host, password, port=port)
        mcr.connect()
        return mcr

    try:
        mcr = try_connect()
    except Exception as e:
        print(Ansi.error(f"RCON 连接失败: {e}"))
        return

    try:
        send_fancy_announce(mcr, player, song)
        time.sleep(0.5)

        mcr.command(f"tp {player} {start_x} {start_y+5} {start_z}")
        for i in range(3, 0, -1):
            mcr.command(f'title {player} actionbar {{"text":"倒计时 {i} 秒","color":"gold"}}')
            time.sleep(1)
        mcr.command(f'title {player} actionbar {{"text":"开始生成！","color":"green"}}')
        time.sleep(0.5)
        mcr.command(f'tp {player} {start_x} {start_y} {start_z}')

        count = 0
        start_time = time.time()
        last_percent = -1
        last_actionbar_time = 0
        failed_cmds = []

        idx = 0
        while idx < len(commands):
            cmd = commands[idx]
            if cmd.startswith('#'):
                idx += 1
                continue

            retries = 0
            success = False
            while retries < max_retries:
                try:
                    full = f"execute at {player} run {cmd}"
                    mcr.command(full)
                    success = True
                    break
                except Exception as e:
                    retries += 1
                    print(Ansi.error(f"命令失败 (重试 {retries}/{max_retries}): {cmd[:50]}... 错误: {e}"))
                    time.sleep(1)
                    if retries < max_retries:
                        try:
                            mcr.disconnect()
                        except:
                            pass
                        try:
                            mcr = try_connect()
                            print(Ansi.info("RCON 已重连"))
                        except Exception as ce:
                            print(Ansi.error(f"重连失败: {ce}"))
                            if retries == max_retries - 1:
                                failed_cmds.extend([commands[i] for i in range(idx, len(commands)) if not commands[i].startswith('#')])
                                idx = len(commands)
                                break

            if not success:
                failed_cmds.append(cmd)

            if idx > 1:
                if cmd.startswith("fill") and not commands[idx-1].startswith("fill"):
                    parts = cmd.split()
                    if len(parts) >= 7:
                        try:
                            x2, y2, z2 = int(parts[4]), int(parts[5]), int(parts[6])
                            mcr.command(f"tp {player} {x2} {y2+4} {z2}")
                        except:
                            pass

            count += 1
            percent = int(count / total * 100) if total > 0 else 0
            now = time.time()
            if percent != last_percent or (now - last_actionbar_time) >= 0.2:
                elapsed = now - start_time
                eta_seconds = int((elapsed / count) * (total - count)) if count > 0 else 0
                eta_str = format_time(eta_seconds)
                actionbar_msg = [
                    {"text": "PipeMusic NBS生成器 ", "color": "red"},
                    {"text": "| ", "color": "white"},
                    {"text": "进度: ", "color": "yellow"},
                    {"text": f"{percent}% ", "color": "white"},
                    {"text": f"[{count}/{total}] ", "color": "white"},
                    {"text": "| ", "color": "white"},
                    {"text": "预计时间: ", "color": "gold"},
                    {"text": eta_str, "color": "light_purple"}
                ]
                try:
                    mcr.command(f'title {player} actionbar {json.dumps(actionbar_msg, ensure_ascii=False)}')
                except:
                    pass
                last_percent = percent
                last_actionbar_time = now

            idx += 1

        done_msg = [
            {"text": "PipeMusic NBS生成器 ", "color": "red"},
            {"text": "| ", "color": "white"},
            {"text": "生成成功！", "color": "green", "bold": True}
        ]
        try:
            mcr.command(f'title {player} actionbar {json.dumps(done_msg, ensure_ascii=False)}')
            mcr.command('say 红石音乐链生成完毕！')
        except:
            pass

        if failed_cmds:
            with open(log_file, 'w', encoding='utf-8') as f:
                f.write('\n'.join(failed_cmds))
            print(Ansi.error(f"有 {len(failed_cmds)} 条命令失败，已记录至 {log_file}"))
        else:
            print(Ansi.success("所有命令已成功发送"))

    finally:
        try:
            mcr.disconnect()
        except:
            pass


# ── UI 交互（支持立体声设置）───────────────────────────────────
def show_tracks_virtual(virtual_layers: List[Dict], layer_to_group: Dict[int, int], group_staircase_modes: Dict[str, str] = None):
    print(Ansi.title("────────── 当前音轨分组（虚拟层）──────────"))
    groups = defaultdict(list)
    for vid, gid in layer_to_group.items():
        groups[gid].append(vid)

    if group_staircase_modes is None:
        group_staircase_modes = {}

    # 构建虚拟层信息查找
    vinfo = {v['vid']: v for v in virtual_layers}

    import re
    ansi_escape = re.compile(r'\x1B(?:[@-Z\\-_]|\[[0-?]*[ -/]*[@-~])')
    def visible_len(s):
        return len(ansi_escape.sub('', s))

    lines = []
    for gid in sorted(groups):
        vids = sorted(groups[gid])
        parts = []
        for vid in vids:
            info = vinfo[vid]
            orig_id = info['original_id']
            side = info['side']
            layer_obj = next((l for l in song.layers if l.id == orig_id), None)
            name = layer_obj.name if layer_obj and layer_obj.name else f"音轨{orig_id}"
            if side == 'stereo':
                name_str = Ansi.colorize(f"{name} (立体声)", Ansi.WHITE)
            elif side == 'left':
                name_str = Ansi.colorize(f"{name} (左)", Ansi.WHITE)
            elif side == 'right':
                name_str = Ansi.colorize(f"{name} (右)", Ansi.WHITE)
            else:
                name_str = Ansi.colorize(name, Ansi.WHITE)
            cat = info['cat']
            if cat != "钢琴":
                cat_str = Ansi.colorize(f"({cat})", Ansi.BLUE)
                parts.append(f"{name_str}{cat_str}")
            else:
                parts.append(name_str)
        group_header = Ansi.colorize(f"[组{gid+1}]", Ansi.CYAN, bold=True)
        # 显示阶梯模式标签
        mode = group_staircase_modes.get(str(gid), "default")
        if mode == "staircase":
            mode_tag = Ansi.colorize(" [阶梯↓]", Ansi.YELLOW)
        else:
            mode_tag = Ansi.colorize(" [默认]", Ansi.DIM)
        line = group_header + mode_tag + " " + ", ".join(parts)
        lines.append(line)

    if not lines:
        return

    term_width = os.get_terminal_size().columns
    max_line_len = max(visible_len(l) for l in lines)
    col_spacing = 2
    col_width = max_line_len + col_spacing
    cols = max(1, term_width // col_width)
    rows = (len(lines) + cols - 1) // cols

    for row in range(rows):
        line_parts = []
        for col in range(cols):
            idx = row + col * rows
            if idx < len(lines):
                raw = lines[idx]
                raw_len = visible_len(raw)
                padding = col_width - raw_len
                line_parts.append(raw + ' ' * padding)
        print(''.join(line_parts).rstrip())

def edit_groups_interactive_virtual(virtual_layers: List[Dict]) -> Dict[int, int]:
    print(Ansi.title("\n=== 音轨分组编辑（虚拟层） ==="))
    print(Ansi.info("可用虚拟层："))
    for v in virtual_layers:
        orig_id = v['original_id']
        side = v['side']
        name = f"音轨{orig_id}"
        if side == 'stereo':
            name += " (立体声)"
        elif side == 'left':
            name += " (左)"
        elif side == 'right':
            name += " (右)"
        print(f"  {v['vid']}: {Ansi.colorize(name, Ansi.WHITE)} ({v['cat']})")
    print(Ansi.dim("输入2~3个虚拟层序号合并，空格分隔，输入 'auto' 自动分组\n未输入的将单独成组"))
    pairs = []
    while True:
        inp = input(Ansi.prompt("合并虚拟层 (回车结束): ")).strip()
        if not inp:
            break
        if inp.lower() == "auto":
            print("选择自动合并模式：")
            print("    [1] 同乐器优先")
            print("    [2] 最大化合并")
            m = input(Ansi.prompt("模式: ")).strip()
            mode = int(m) if m in ('1','2') else 1
            layer_to_group = auto_group_by_mode_on_layers(virtual_layers, mode)
            print(Ansi.success(f"自动分组完成，共 {len(set(layer_to_group.values()))} 组"))
            return layer_to_group
        parts = inp.split()
        if len(parts) not in (2, 3):
            print(Ansi.error("请输入2或3个数字"))
            continue
        try:
            ids = [int(p) for p in parts]
        except ValueError:
            print(Ansi.error("无效数字"))
            continue
        if not all(any(v['vid']==i for v in virtual_layers) for i in ids):
            print(Ansi.error("序号不存在"))
            continue
        pairs.append(tuple(sorted(ids)))

    layer_to_group = {}
    group_id = 0
    assigned = set()
    for ids in pairs:
        if any(i in assigned for i in ids):
            print(Ansi.error(f"重复分配，跳过: {ids}"))
            continue
        for i in ids:
            layer_to_group[i] = group_id
        assigned.update(ids)
        group_id += 1
    for v in virtual_layers:
        if v['vid'] not in assigned:
            layer_to_group[v['vid']] = group_id
            group_id += 1
    print(Ansi.success("分组更新完毕"))
    return layer_to_group

def edit_layout(num_groups: int, config: dict, layer_to_group: Dict[int, int], virtual_layers: List[Dict]):
    print(Ansi.title("── 排版样式设置 ──"))
    print("[1] 平铺")
    print("[2] 圆形")
    print("[3] 方形")
    print("[4] 半圆")
    print("[5] 半方形")
    print("[6] 嵌套圆")
    print("[7] 嵌套方")
    style_map = {"1": "flat", "2": "circle", "3": "square", "4": "semicircle",
                 "5": "semisquare", "6": "nest_circle", "7": "nest_square"}
    choice = input(Ansi.prompt("请选择样式 (1-7): ")).strip()
    if choice not in style_map:
        print(Ansi.error("无效，使用当前样式"))
        return
    config["layout_style"] = style_map[choice]
    style = config["layout_style"]

    if style != "flat":
        print(Ansi.info("当前分组（虚拟层）："))
        temp_groups = defaultdict(list)
        for vid, gid in layer_to_group.items():
            temp_groups[gid].append(vid)
        vinfo = {v['vid']: v for v in virtual_layers}
        for gid in sorted(temp_groups):
            vids = sorted(temp_groups[gid])
            parts = []
            for vid in vids:
                info = vinfo[vid]
                orig_id = info['original_id']
                side = info['side']
                layer_obj = next((l for l in song.layers if l.id == orig_id), None)
                name = layer_obj.name if layer_obj and layer_obj.name else f"音轨{orig_id}"
                if side == 'stereo':
                    name_str = Ansi.colorize(f"{name} (立体声)", Ansi.WHITE)
                elif side == 'left':
                    name_str = Ansi.colorize(f"{name} (左)", Ansi.WHITE)
                elif side == 'right':
                    name_str = Ansi.colorize(f"{name} (右)", Ansi.WHITE)
                else:
                    name_str = Ansi.colorize(name, Ansi.WHITE)
                cat = info['cat']
                if cat != "钢琴":
                    cat_str = Ansi.colorize(f"({cat})", Ansi.BLUE)
                    parts.append(f"{name_str}{cat_str}")
                else:
                    parts.append(name_str)
            group_header = Ansi.colorize(f"[组{gid+1}]", Ansi.CYAN, bold=True)
            print(group_header + " " + ", ".join(parts))

        master = input(Ansi.prompt("核心组ID (留空则无): ")).strip()
        if master:
            try:
                config["master_group"] = int(master) - 1
            except:
                config["master_group"] = None
        else:
            config["master_group"] = None

        has_master = config["master_group"] is not None
        min_r = compute_min_radius(num_groups, style, has_master)
        print(Ansi.info(f"最小允许半径/边长: {min_r}"))

        if style in ("nest_circle", "nest_square"):
            max_r = config.get("layout_radius", 10)
            max_layers = calc_nest_layers(num_groups, max_r, config.get("layer_gap", 4))
            print(Ansi.info(f"最大可嵌套层数: {max_layers}"))
            while True:
                try:
                    layers = int(input(Ansi.prompt(f"嵌套层数 (1~{max_layers}): ")))
                    if 1 <= layers <= max_layers:
                        config["nest_layers"] = layers
                        break
                except:
                    pass
                print(Ansi.error("输入无效"))
            try:
                gap = int(input(Ansi.prompt("层间距 (默认4): ") or "4"))
                config["layer_gap"] = max(1, gap)
            except:
                config["layer_gap"] = 4

        while True:
            try:
                radius = int(input(Ansi.prompt(f"半径/边长 (≥{min_r}): ") or str(min_r)))
                if radius < min_r:
                    print(Ansi.error(f"半径不得小于 {min_r}"))
                    continue
                config["layout_radius"] = radius
                break
            except:
                print(Ansi.error("请输入整数"))
    else:
        try:
            spacing = int(input(Ansi.prompt("平铺间距 (默认3): ") or "3"))
            config["flat_spacing"] = spacing
        except:
            pass

    save_config(config)
    print(Ansi.success("排版设置已更新"))

def edit_stereo_layers(song, config):
    """交互式选择立体声音轨"""
    print(Ansi.title("\n=== 立体声轨道设置 ==="))
    print(Ansi.info("选择哪些原始音轨作为立体声（左右声道独立）。"))
    print("立体声轨道的音符会同时出现在左右两条链上，并在排版中对称分布。")
    print(Ansi.dim("当前可用的音轨（原始 layer ID）："))
    # 获取所有非空音轨
    available_layers = sorted({note.layer for note in song.notes})
    for lid in available_layers:
        layer_obj = next((l for l in song.layers if l.id == lid), None)
        name = layer_obj.name if layer_obj and layer_obj.name else f"音轨{lid}"
        inst, cat = analyze_track_instrument(song, lid)
        print(f"  {lid}: {Ansi.colorize(name, Ansi.WHITE)} ({cat})")
    print(Ansi.prompt("请输入要设为立体声的音轨ID，多个用空格分隔，直接回车跳过："))
    inp = input().strip()
    stereo = []
    if inp:
        parts = inp.split()
        for p in parts:
            try:
                lid = int(p)
                if lid in available_layers:
                    stereo.append(lid)
                else:
                    print(Ansi.error(f"忽略无效音轨ID: {lid}"))
            except:
                pass
    config['stereo_layers'] = stereo
    save_config(config)
    if stereo:
        print(Ansi.success(f"已设置立体声音轨: {stereo}"))
    else:
        print(Ansi.info("已清除立体声设置（所有音轨为单声道）"))

def edit_tp_player(config):
    """交互式设置要自动传送的玩家名"""
    print(Ansi.title("\n=== 自动传送玩家设置 ==="))
    print(Ansi.info("开启后，生成结构时会额外放置一个命令方块链，"))
    print("在结构激活时自动将指定玩家传送到中心轴上方2格。")
    current = config.get("tp_player", "")
    if current:
        print(Ansi.info(f"当前设置的玩家: {current}"))
    else:
        print(Ansi.dim("当前未启用自动传送"))
    new_name = input(Ansi.prompt("请输入玩家名 (留空禁用): ")).strip()
    if new_name:
        config["tp_player"] = new_name
        print(Ansi.success(f"已设置，将传送玩家 '{new_name}' 到中心轴上方"))
    else:
        config["tp_player"] = ""
        print(Ansi.info("已禁用自动传送"))
    save_config(config)

def edit_group_staircase_modes(config, layer_to_group, virtual_layers):
    """交互式设置每个分组的生成模式（默认/阶梯向下）"""
    print(Ansi.title("\n=== 分组生成模式设置 ==="))
    print(Ansi.info("为每个分组独立设置生成模式："))
    print("  [默认] - 所有音符在同一水平面（默认模式）")
    print("  [阶梯↓] - 侧边音符向下降一格，形成阶梯结构")
    print()
    group_staircase_modes = config.get("group_staircase_modes", {})
    groups = sorted(set(layer_to_group.values()))

    # 显示当前状态
    vinfo = {v['vid']: v for v in virtual_layers}
    for gid in groups:
        vids = sorted([vid for vid, g in layer_to_group.items() if g == gid])
        parts = []
        for vid in vids:
            info = vinfo[vid]
            orig_id = info['original_id']
            layer_obj = next((l for l in song.layers if l.id == orig_id), None)
            name = layer_obj.name if layer_obj and layer_obj.name else f"音轨{orig_id}"
            parts.append(name)
        current_mode = group_staircase_modes.get(str(gid), "default")
        mode_display = Ansi.colorize("阶梯↓", Ansi.YELLOW) if current_mode == "staircase" else Ansi.colorize("默认", Ansi.DIM)
        group_label = Ansi.colorize(f"[组{gid+1}]", Ansi.CYAN, bold=True)
        print(f"  {group_label} {mode_display}  ({', '.join(parts)})")

    print()
    print(Ansi.prompt("输入要切换模式的组ID（用空格分隔，回车跳过）："))
    inp = input().strip()
    if not inp:
        return

    for part in inp.split():
        try:
            gid = int(part) - 1  # 用户输入1-based，转换为0-based
            if gid in groups:
                current = group_staircase_modes.get(str(gid), "default")
                new_mode = "staircase" if current != "staircase" else "default"
                group_staircase_modes[str(gid)] = new_mode
                display = Ansi.colorize("阶梯↓", Ansi.YELLOW) if new_mode == "staircase" else Ansi.colorize("默认", Ansi.DIM)
                print(Ansi.success(f"组{gid+1} 已切换为 {display}"))
            else:
                print(Ansi.error(f"组{gid+1} 不存在，跳过"))
        except ValueError:
            print(Ansi.error(f"无效输入: {part}"))

    config["group_staircase_modes"] = group_staircase_modes
    save_config(config)

# ── 主函数 ─────────────────────────────────────────────────
def main():
    global song  # 为了让 show_tracks_virtual 访问
    os.system('cls' if os.name == 'nt' else 'clear')
    config = load_config()
    print(Ansi.title("══════ NBS生成器 - PipeMusic (阶梯模式版) ══════"))
    print(Ansi.dim("作者: COM1919 | 版本: 3.3 (立体声+阶梯模式+传送链)"))

    from input_completer import input_nbs_path
    nbs_path = input_nbs_path(Ansi.prompt("NBS 文件路径: ") or "song.nbs").strip()
    try:
        song = nbs_read(nbs_path)
    except Exception as e:
        print(Ansi.error(f"读取 NBS 失败: {e}"))
        sys.exit(1)

    if not song.notes:
        print(Ansi.error("错误：NBS 文件中没有任何音符，无法生成红石音乐链。"))
        sys.exit(1)
        
    # 2. 询问用户是否按乐器排序
    print(Ansi.prompt("\n请选择后续处理模式："))
    print("  [1] 压缩后按乐器类型排序（打击乐在后，每个音轨一种乐器）")
    print("  [2] 不处理")
    mode = input(Ansi.prompt("请输入 (1/2): ")).strip()
    if mode == "1":
        print(Ansi.info("正在执行垂直压缩..."))
        song = vertical_compress(song)
        print(Ansi.success(f"压缩完成，原始音轨数：{len(song.layers)}"))
        print(Ansi.info("正在按乐器类型重新排列音轨..."))
        song = rearrange_tracks_by_instrument(song)
        print(Ansi.success("乐器排序完成！"))
        config["merge_groups"] = []
        save_config(config)
    elif mode == "2":
        print(Ansi.info("好的"))
    else:
        print(Ansi.error("无效输入，默认跳过"))

    # 3. 确保有一个空音轨
    #song = ensure_one_empty_layer(song)
    #print(Ansi.info(f"最终原始音轨数量（含空音轨）：{len(song.layers)}"))

    # 4. 输出基本信息
    max_layer = max(note.layer for note in song.notes) if song.notes else 0
    print(Ansi.info(f"曲名: {song.header.song_name or '未知'}"))
    print(Ansi.info(f"音符总数: {len(song.notes)}"))
    print(Ansi.info(f"原始音轨数量: {max_layer+1}"))
    print(Ansi.info(f"音乐作者: {song.header.original_author}"))
    print(Ansi.info(f"描述: {song.header.description}"))

    # 5. 构建虚拟层（基于立体声配置，正确方式：复制音符）
    stereo_set = set(config.get('stereo_layers', []))
    virtual_layers = []
    next_vid = 0
    for orig_layer in song.layers:
        lid = orig_layer.id
        notes = [n for n in song.notes if n.layer == lid]
        if not notes:
            continue  # 跳过空音轨
        inst, cat = analyze_track_instrument(song, lid)
        if lid in stereo_set:
            # 立体声：复制每个音符一次，使每个 tick 有两个相同音符
            stereo_notes = []
            for note in notes:
                stereo_notes.append(note)
                stereo_notes.append(note)      # 复制一份
            stereo_notes.sort(key=lambda n: n.tick)   # 确保同一 tick 的两个音符相邻
            virtual_layers.append({
                'vid': next_vid,
                'original_id': lid,
                'side': 'stereo',
                'notes': stereo_notes,
                'inst': inst,
                'cat': cat
            })
            next_vid += 1
        else:
            virtual_layers.append({
                'vid': next_vid,
                'original_id': lid,
                'side': None,
                'notes': notes,
                'inst': inst,
                'cat': cat
            })
            next_vid += 1

    if not virtual_layers:
        print(Ansi.error("没有找到任何有音符的虚拟层，无法生成。"))
        sys.exit(1)

    print(Ansi.success(f"已创建 {len(virtual_layers)} 个虚拟层（立体声效果：{len(stereo_set)} 个音轨被立体声化）"))

    # 6. 交互循环
    layer_to_group = {v['vid']: idx for idx, v in enumerate(virtual_layers)}
    is_first_show_text = True
    while True:
        if is_first_show_text:
            is_first_show_text = False
        else:
            input("按回车键继续...")
        os.system('cls' if os.name == 'nt' else 'clear')

        print(Ansi.title("══════ 当前配置 ══════"))
        print(Ansi.colorize("[RCON]", Ansi.MAGENTA))
        print(f"  主机: {config['rcon_host']}:{config['rcon_port']}")
        print(f"  玩家: {config['player_name']}")
        print(f"  线程数: {config.get('num_threads', 4)}")
        print(Ansi.colorize("[生成参数]", Ansi.MAGENTA))
        print(f"  方向: {config['direction']}")
        style = config.get("layout_style", "flat")
        print(f"  排版: {style}", end="")
        if style == "flat":
            print(f" 间距={config.get('flat_spacing', 3)}")
        elif style in ("nest_circle", "nest_square"):
            print(f" 半径={config.get('layout_radius', 5)} 层数={config.get('nest_layers', 1)}")
        else:
            print(f" 半径={config.get('layout_radius', 5)}", end="")
            if config.get("master_group") is not None:
                print(f" 核心组={config['master_group']+1}", end="")
            print()
        print(Ansi.colorize("[立体声]", Ansi.MAGENTA))
        stereo_layers = config.get('stereo_layers', [])
        if stereo_layers:
            print(f"  立体声音轨: {stereo_layers}")
        else:
            print(f"  无立体声")
        print(Ansi.colorize("[传送玩家]", Ansi.MAGENTA))
        tp_player = config.get("tp_player", "")
        if tp_player:
            print(f"  自动传送: {tp_player}")
        else:
            print(f"  未启用")
        print(Ansi.colorize("[分组生成模式]", Ansi.MAGENTA))
        gsm = config.get("group_staircase_modes", {})
        stair_groups = [str(int(k)+1) for k, v in gsm.items() if v == "staircase"]
        if stair_groups:
            print(f"  阶梯模式组: {', '.join(stair_groups)}")
        else:
            print(f"  全部使用默认模式")

        show_tracks_virtual(virtual_layers, layer_to_group, gsm)

        print(Ansi.title("──────── 菜单 ────────"))
        print("[1] 修改 RCON")
        print("[2] 修改方向")
        print("[3] 修改音轨分组（虚拟层）")
        print("[4] 修改排版")
        print("[5] 保存并退出")
        print("[6] 设置立体声音轨")
        print("[7] 切换中继器均匀速度模式 " + ("(已启用)" if config.get("uniform_repeater_mode", False) else "(已禁用)"))
        print("[8] 设置自动传送玩家")
        print("[9] 设置分组生成模式（默认/阶梯向下）")
        choice = input(Ansi.prompt("选择 (回车开始生成): ")).strip()

        if choice == "1":
            config["rcon_host"] = input(Ansi.prompt(f"IP ({config['rcon_host']}): ") or config["rcon_host"])
            try:
                config["rcon_port"] = int(input(Ansi.prompt(f"端口 ({config['rcon_port']}): ") or config["rcon_port"]))
            except:
                pass
            config["rcon_password"] = input(Ansi.prompt("密码: ") or config["rcon_password"])
            config["player_name"] = input(Ansi.prompt(f"玩家 ({config['player_name']}): ") or config["player_name"])
            save_config(config)
        elif choice == "2":
            d = input(Ansi.prompt(f"方向 ({config['direction']}): ") or config["direction"]).lower()
            if d in DIR_OFFSET:
                config["direction"] = d
                save_config(config)
            else:
                print(Ansi.error("无效方向"))
        elif choice == "3":
            layer_to_group = edit_groups_interactive_virtual(virtual_layers)
            save_config(config)
        elif choice == "4":
            num_groups = len(set(layer_to_group.values()))
            edit_layout(num_groups, config, layer_to_group, virtual_layers)
        elif choice == "5":
            save_config(config)
            print(Ansi.success("再见！"))
            break
        elif choice == "6":
            edit_stereo_layers(song, config)
            # 重新构建虚拟层（使用正确方法）
            stereo_set = set(config.get('stereo_layers', []))
            virtual_layers = []
            next_vid = 0
            for orig_layer in song.layers:
                lid = orig_layer.id
                notes = [n for n in song.notes if n.layer == lid]
                if not notes:
                    continue
                inst, cat = analyze_track_instrument(song, lid)
                if lid in stereo_set:
                    stereo_notes = []
                    for note in notes:
                        stereo_notes.append(note)
                        stereo_notes.append(note)
                    stereo_notes.sort(key=lambda n: n.tick)
                    virtual_layers.append({
                        'vid': next_vid,
                        'original_id': lid,
                        'side': 'stereo',
                        'notes': stereo_notes,
                        'inst': inst,
                        'cat': cat
                    })
                    next_vid += 1
                else:
                    virtual_layers.append({
                        'vid': next_vid,
                        'original_id': lid,
                        'side': None,
                        'notes': notes,
                        'inst': inst,
                        'cat': cat
                    })
                    next_vid += 1
            # 重置分组
            layer_to_group = {v['vid']: idx for idx, v in enumerate(virtual_layers)}
            print(Ansi.success(f"立体声设置已更新，重新创建了 {len(virtual_layers)} 个虚拟层"))
        elif choice == "7":
            current = config.get("uniform_repeater_mode", False)
            config["uniform_repeater_mode"] = not current
            save_config(config)
            print(Ansi.success(f"中继器填充模式已{'启用' if config['uniform_repeater_mode'] else '禁用'}"))
        elif choice == "8":
            edit_tp_player(config)
        elif choice == "9":
            edit_group_staircase_modes(config, layer_to_group, virtual_layers)
        elif not choice:
            max_in_file = max(n.tick for n in song.notes) if song.notes else 0
            try:
                max_tick = int(input(Ansi.prompt(f"希望生成的长度 (≤{max_in_file} 回车默认): ")))
            except:
                print(Ansi.error("输入错误"))
                continue

            # 放置方式选择
            print(Ansi.prompt("\n请选择放置方式："))
            print("  [1] 通过 RCON 直接放置")
            print("  [2] 生成 .schem 结构文件（可用于 WorldEdit）")
            print("  [3] 仅生成命令文本文件（不放置）")
            place_choice = input(Ansi.prompt("请输入 (1/2/3): ")).strip()
            if place_choice not in ('1','2','3'):
                print(Ansi.error("无效选择，默认使用仅生成命令文本"))
                place_choice = '3'

            # 获取起始坐标
            start_x = start_y = start_z = None
            if place_choice == '1':
                print(Ansi.info("提示：直接回车将使用玩家位置作为起始坐标"))
                start_x_str = input(Ansi.prompt("起始 X (回车=自动): ")).strip()
                if not start_x_str:
                    player = config["player_name"]
                    try:
                        mcr = MCRcon(config["rcon_host"], config["rcon_password"], port=config["rcon_port"])
                        mcr.connect()
                    except Exception as e:
                        print(Ansi.error(f"RCON 连接失败: {e}"))
                        continue

                    try:
                        raw = mcr.command(f"data get entity {player} Pos")
                        start_idx = raw.find('[')
                        end_idx = raw.rfind(']')
                        if start_idx == -1 or end_idx == -1 or end_idx <= start_idx:
                            print(Ansi.error(f"无法解析玩家坐标，服务器返回: {raw}"))
                            mcr.disconnect()
                            continue
                        clean = raw[start_idx:end_idx+1]
                        nums = clean[1:-1].split(",")
                        if len(nums) < 3:
                            print(Ansi.error(f"坐标数据不完整: {raw}"))
                            mcr.disconnect()
                            continue
                        start_x = round(float(nums[0].replace("d", "").strip()))
                        start_y = round(float(nums[1].replace("d", "").strip()))
                        start_z = round(float(nums[2].replace("d", "").strip()))
                        mcr.disconnect()
                        print(Ansi.success(f"已获取玩家位置: X={start_x} Y={start_y} Z={start_z}"))
                    except Exception as e:
                        print(Ansi.error(f"获取玩家位置失败，请确认玩家 {player} 在线。错误: {e}"))
                        try:
                            mcr.disconnect()
                        except:
                            pass
                        print(Ansi.info("请手动输入起始坐标："))
                        try:
                            start_x = int(input(Ansi.prompt("起始 X: ")))
                            start_y = int(input(Ansi.prompt("起始 Y: ")))
                            start_z = int(input(Ansi.prompt("起始 Z: ")))
                        except:
                            print(Ansi.error("坐标输入错误"))
                            continue
                else:
                    try:
                        start_x = int(start_x_str)
                        start_y = int(input(Ansi.prompt("起始 Y: ")))
                        start_z = int(input(Ansi.prompt("起始 Z: ")))
                    except:
                        print(Ansi.error("坐标必须为整数"))
                        continue
            elif place_choice == '2':
                start_x, start_y, start_z = 0, 0, 0
            else:
                print(Ansi.info("请输入起始坐标（用于生成命令中的绝对坐标）"))
                try:
                    start_x = int(input(Ansi.prompt("起始 X: ")))
                    start_y = int(input(Ansi.prompt("起始 Y: ")))
                    start_z = int(input(Ansi.prompt("起始 Z: ")))
                except:
                    print(Ansi.error("坐标错误"))
                    continue

            # 计算分组
            groups = sorted(set(layer_to_group.values()))
            num_groups = len(groups)

            # 计算偏移量（移除了立体声配对参数）
            offsets = compute_offsets(groups, config, num_groups)
            group_offset_dict = {gid: offsets[i] for i, gid in enumerate(groups)}

            print(Ansi.info("正在预计算方块位置..."))
            use_lamp = config.get("use_redstone_lamp", False)
            commands, activation_positions = build_all_commands_virtual(
                virtual_layers, layer_to_group, [group_offset_dict[gid] for gid in groups],
                (start_x, start_y, start_z), config["direction"], max_tick,
                use_lamp=use_lamp,
                uniform_repeater_mode=config.get("uniform_repeater_mode", False),
                group_staircase_modes=config.get("group_staircase_modes", {})
            )
            # 添加命令方块启动链
            dx, dy, dz = DIR_OFFSET[config["direction"]]
            cb1_x = start_x - 5*dx
            cb1_y = start_y
            cb1_z = start_z if config["direction"] in ("east", "west") else start_z - 5*dz

            cb2_x = start_x - 7*dx
            cb2_y = start_y
            cb2_z = start_z if config["direction"] in ("east", "west") else start_z - 7*dz

            sorted_groups = sorted(activation_positions.items(), key=lambda x: x[0])
            cmdblock_cmds = []
            def rel_str(d: int) -> str:
                if d == 0:
                    return "~"
                else:
                    return f"~{d}"
            # 在循环中计算每组命令方块的相对偏移
            for i, (gid, (ax, ay, az)) in enumerate(sorted_groups):
                # 放置柱（第一根柱子）坐标
                if i == 0:
                    cx1, cy1, cz1 = cb1_x, cb1_y, cb1_z
                    cx2, cy2, cz2 = cb2_x, cb2_y, cb2_z
                else:
                    cx1, cy1, cz1 = cb1_x, cb1_y + i, cb1_z
                    cx2, cy2, cz2 = cb2_x, cb2_y + i, cb2_z
            
                # 计算相对于放置柱命令方块的偏移（用于放置红石块的命令）
                rel_x_place = ax - cx1
                rel_y_place = ay - cy1
                rel_z_place = az - cz1
                # 计算相对于清除柱命令方块的偏移（用于清除红石块的命令）
                rel_x_remove = ax - cx2
                rel_y_remove = ay - cy2
                rel_z_remove = az - cz2
            
                # 生成相对坐标命令
                place_cmd = f"setblock {rel_str(rel_x_place)} {rel_str(rel_y_place)} {rel_str(rel_z_place)} minecraft:redstone_block"
                remove_cmd = f"setblock {rel_str(rel_x_remove)} {rel_str(rel_y_remove)} {rel_str(rel_z_remove)} minecraft:air"
            
                place_json = json.dumps(place_cmd)
                remove_json = json.dumps(remove_cmd)
            
                if i == 0:
                    cmdblock_cmds.append(
                        f"setblock {cx1} {cy1} {cz1} minecraft:command_block[facing=up]{{Command:{place_json}}}"
                    )
                    cmdblock_cmds.append(
                        f"setblock {cx2} {cy2} {cz2} minecraft:command_block[facing=up]{{Command:{remove_json}}}"
                    )
                else:
                    cmdblock_cmds.append(
                        f"setblock {cx1} {cy1} {cz1} minecraft:chain_command_block[facing=up,conditional=false]{{Command:{place_json},auto:1b}}"
                    )
                    cmdblock_cmds.append(
                        f"setblock {cx2} {cy2} {cz2} minecraft:chain_command_block[facing=up,conditional=false]{{Command:{remove_json},auto:1b}}"
                    )
            commands = commands[:1] + cmdblock_cmds + commands[1:]

            # ─────────────────────────────────────────────────────────────
            # 新增：自动传送玩家链（单独的命令方块组）
            tp_player = config.get("tp_player", "")
            if tp_player:
                # 中心轴下方2格放置一个 impulse 命令方块，自动激活（无需红石）
                # 命令方块位置：起始点 (start_x, start_y-2, start_z)
                # 传送目标：起始点上方2格 (start_x, start_y+2, start_z)
                cb_tp_x = start_x
                cb_tp_y = start_y - 2
                cb_tp_z = start_z
                tp_cmd = f"tp {tp_player} {start_x} {start_y+2} {start_z}"
                # 使用 impulse 命令方块，auto:1b 表示生成后自动激活一次
                commands.append(f"setblock {cb_tp_x} {cb_tp_y} {cb_tp_z} minecraft:command_block[facing=up]{{Command:\"{tp_cmd}\",auto:1b}}")
                print(Ansi.success(f"已添加自动传送链：将传送玩家 '{tp_player}' 到 ({start_x},{start_y+2},{start_z})"))
            # ─────────────────────────────────────────────────────────────

            # 输出命令文件
            out_file = "nbs_output_cmd.txt"
            with open(out_file, 'w', encoding='utf-8') as f:
                f.write('\n'.join(commands))
            print(Ansi.success(f"主命令已保存至 {out_file}"))

            if place_choice == '1':
                send_via_rcon(
                    commands,
                    config["rcon_host"], config["rcon_port"],
                    config["rcon_password"], config["player_name"],
                    config["direction"], song, start_x, start_y, start_z
                )
            elif place_choice == '2':
                schem_path = input(Ansi.prompt("保存结构文件的路径 (默认 ./redstone_music.schem): ")).strip()
                if not schem_path:
                    schem_path = "./redstone_music.schem"
                generate_schematic_from_commands(commands, schem_path)
            else:
                print(Ansi.info("已跳过放置，命令文本保存在 nbs_output_cmd.txt"))

            input("按回车返回菜单...")
        else:
            print(Ansi.error("无效选项"))

if __name__ == "__main__":
    main()