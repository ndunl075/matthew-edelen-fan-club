from __future__ import annotations
import json
import queue
import sys
import threading
import traceback
from typing import Any, Dict, List, Literal, Optional, TypedDict, Union
try:
    from typing import NotRequired
except ImportError:
    pass
BotMessage = Union['Ready', 'Action']
Color = str
EffectName = Literal['cookoff', 'short', 'concuss']
GameEvent = Union['DamagedEvent', 'HitEvent', 'EffectEvent', 'DropEvent', 'KillEvent', 'PickupEvent', 'UpgradeEvent', 'TookEvent', 'DroppedEvent', 'TakeFailedEvent']
MechStats = Dict[str, float]
ServerMessage = Union['Hello', 'Observation', 'EndMsg']
Vec2 = List[float]
Vec3 = List[float]
WeaponKind = Literal['gun', 'mortar', 'laser', 'lightning']

class Action(TypedDict):
    type: NotRequired[Literal['action']]
    tick: float
    move: NotRequired[Vec2]
    boost: NotRequired[bool]
    jump: NotRequired[bool]
    aim: NotRequired[Vec2]
    fire: NotRequired[List[bool]]
    reload: NotRequired[bool]
    pick: NotRequired[float]
    take: NotRequired[float]
    drop: NotRequired[float]
    debug: NotRequired[DebugDraw]

class ActorObs(TypedDict):
    id: float
    kind: Literal['mech', 'npc']
    pos: Vec3
    vel: Vec3
    aim: Vec2
    hp: NotRequired[float]
    max_hp: NotRequired[float]
    cubes: NotRequired[float]
    weapons: NotRequired[List[WeaponKind]]
    charging: NotRequired[bool]

class BeamObs(TypedDict):
    by: float
    kind: Literal['laser', 'lightning']
    path: List[Vec3]
    age: float

class ConfigVision(TypedDict):
    range: float
    fov_deg: float
    near_range: float
    canopy_blocks: bool
    show_enemy_hp: bool
    memory_s: NotRequired[float]

class ConfigSound(TypedDict):
    bearing_noise_deg: float
    near: float
    mid: float
    step_speed: float
    step_every: float
    range: Dict[str, float]

class ConfigWeapons(TypedDict):
    gun: Dict[str, float]
    mortar: Dict[str, float]
    laser: Dict[str, float]
    lightning: Dict[str, float]

class ConfigCubes(TypedDict):
    upgrade_thresholds: List[float]

class ConfigLootWeights(TypedDict):
    gun: float
    mortar: float
    laser: float
    lightning: float

class ConfigLoot(TypedDict):
    pickup_radius: float
    equip_s: float
    floor_count: float
    floor_roof_fraction: float
    crate_weapon_chance: float
    death_drop: float
    weights: ConfigLootWeights

class ConfigDrops(TypedDict):
    times: List[float]
    fall_s: float
    radius: float
    capture_s: float
    cubes: float
    repair: float
    spread: float

class ConfigZone(TypedDict):
    start_center: List[float]
    start_radius: float
    phases: List[ZonePhase]

class Config(TypedDict):
    match: Dict[str, float]
    bot: Dict[str, float]
    mech: MechStats
    vision: ConfigVision
    sound: ConfigSound
    weapons: ConfigWeapons
    projectile: Dict[str, float]
    cubes: ConfigCubes
    crates: Dict[str, float]
    loot: ConfigLoot
    npc: Dict[str, float]
    drops: NotRequired[ConfigDrops]
    zone: ConfigZone
    replay: Dict[str, float]
    debug: DebugCaps
    upgrades: List[UpgradeDef]

class DamagedEvent(TypedDict):
    type: Literal['damaged']
    amount: float
    weapon: str
    bearing: Optional[float]
    by: Optional[float]
    rear: NotRequired[bool]

class DebugCaps(TypedDict):
    max_lines: float
    max_points: float
    max_text: float
    max_text_chars: float
    max_bytes: float
    max_match_bytes: float
    hold_s: float

class DebugDraw(TypedDict):
    lines: NotRequired[List[DebugLine]]
    points: NotRequired[List[DebugPoint]]
    text: NotRequired[List[DebugText]]
    label: NotRequired[str]

class DebugLine(TypedDict):
    a: Vec3
    b: Vec3
    color: NotRequired[Color]

class DebugPoint(TypedDict):
    at: Vec3
    color: NotRequired[Color]
    size: NotRequired[float]

class DebugText(TypedDict):
    text: str
    at: NotRequired[Vec3]
    color: NotRequired[Color]

class DropEvent(TypedDict):
    type: Literal['drop']
    id: float
    cubes: float

class DropObs(TypedDict):
    id: float
    pos: Vec3
    radius: float
    lands_in: float
    capture_s: float
    progress: float
    holder: Optional[float]

class DroppedEvent(TypedDict):
    type: Literal['dropped']
    id: float
    kind: WeaponKind
    slot: float

class Effect(TypedDict):
    path: str
    mul: NotRequired[float]
    add: NotRequired[float]

class EffectEvent(TypedDict):
    type: Literal['effect']
    effect: EffectName
    by: Optional[float]

class EndMsg(TypedDict):
    type: Literal['end']
    place: float
    kills: float

class HelloMap(TypedDict):
    dir: str
    files: List[str]

class Hello(TypedDict):
    type: Literal['hello']
    protocol: str
    slot: float
    seed: float
    map: HelloMap
    config: Config

class HitEvent(TypedDict):
    type: Literal['hit']
    target: float
    amount: float
    weapon: str
    effect: NotRequired[EffectName]
    rear: NotRequired[bool]

class ItemObs(TypedDict):
    id: float
    pos: Vec3
    hp: NotRequired[float]

class KillEvent(TypedDict):
    type: Literal['kill']
    target: float

class Observation(TypedDict):
    type: Literal['obs']
    tick: float
    t: float
    alive: float
    self: SelfObs
    mechs: List[ActorObs]
    projectiles: List[ProjectileObs]
    crates: List[ItemObs]
    cubes: List[ItemObs]
    pickups: List[PickupObs]
    sounds: List[SoundObs]
    beams: List[BeamObs]
    events: List[GameEvent]
    zone: ZoneObs
    offer: Optional[Offer]
    drops: List[DropObs]

class Offer(TypedDict):
    id: float
    choices: List[OfferChoice]
    expires: float

class OfferChoice(TypedDict):
    id: str
    name: str
    desc: str

class PickupEvent(TypedDict):
    type: Literal['pickup']
    cubes: float

class PickupObs(TypedDict):
    id: float
    kind: WeaponKind
    pos: Vec3

class ProjectileObs(TypedDict):
    id: float
    kind: Literal['gun', 'mortar', 'npc']
    pos: Vec3
    vel: Vec3

class Ready(TypedDict):
    type: Literal['ready']
    name: NotRequired[str]

class SelfObs(TypedDict):
    id: float
    pos: Vec3
    vel: Vec3
    aim: Vec2
    hp: float
    max_hp: float
    energy: float
    max_energy: float
    grounded: bool
    cubes: float
    upgrades: List[str]
    weapons: List[Optional[WeaponObs]]
    in_zone: bool
    stats: Dict[str, float]

class SoundObs(TypedDict):
    kind: str
    bearing: float
    dist: Literal['near', 'mid', 'far']
    age: float

class TakeFailedEvent(TypedDict):
    type: Literal['take_failed']
    id: float
    reason: Literal['gone', 'range', 'have', 'full', 'busy']

class TookEvent(TypedDict):
    type: Literal['took']
    id: float
    kind: WeaponKind
    slot: float

class UpgradeDef(TypedDict):
    id: str
    name: str
    desc: str
    requires: NotRequired[WeaponKind]
    max: float
    effects: List[Effect]

class UpgradeEvent(TypedDict):
    type: Literal['upgrade']
    id: str

class WeaponObs(TypedDict):
    kind: WeaponKind
    cooldown: float
    ammo: NotRequired[float]
    reload: NotRequired[float]
    charge: NotRequired[float]
    stats: Dict[str, float]

class ZoneObs(TypedDict):
    center: Vec2
    radius: float
    next_center: Vec2
    next_radius: float
    shrink_start: float
    shrink_end: float
    dps: float

class ZonePhase(TypedDict):
    hold_s: float
    shrink_s: float
    radius: float
    dps: float

class Draw:

    def __init__(self) -> None:
        self.caps: Dict[str, float] = {}
        self.clear()

    def clear(self) -> None:
        self._d: DebugDraw = {}

    def _room(self, key: str, cap: str) -> bool:
        return len(self._d.get(key, [])) < self.caps.get(cap, 1000000000.0)

    def line(self, a: List[float], b: List[float], color: Optional[str]=None) -> None:
        if self._room('lines', 'max_lines'):
            it: DebugLine = {'a': [float(v) for v in a[:3]], 'b': [float(v) for v in b[:3]]}
            if color:
                it['color'] = color
            self._d.setdefault('lines', []).append(it)

    def point(self, at: List[float], color: Optional[str]=None, size: Optional[float]=None) -> None:
        if self._room('points', 'max_points'):
            it: DebugPoint = {'at': [float(v) for v in at[:3]]}
            if color:
                it['color'] = color
            if size is not None:
                it['size'] = size
            self._d.setdefault('points', []).append(it)

    def text(self, text: str, at: Optional[List[float]]=None, color: Optional[str]=None) -> None:
        if self._room('text', 'max_text'):
            it: DebugText = {'text': str(text)}
            if at is not None:
                it['at'] = [float(v) for v in at[:3]]
            if color:
                it['color'] = color
            self._d.setdefault('text', []).append(it)

    def label(self, text: str) -> None:
        self._d['label'] = str(text)

    def take(self) -> Optional[DebugDraw]:
        d, self._d = (self._d, {})
        return d or None
draw = Draw()
_CONTINUOUS = ('move', 'aim', 'boost')

def log(*args: Any) -> None:
    print(*args, file=sys.stderr, flush=True)

def _send(msg: Dict[str, Any]) -> None:
    sys.stdout.write(json.dumps(msg, separators=(',', ':')) + '\n')
    sys.stdout.flush()

def _reader(q: 'queue.Queue[Optional[str]]') -> None:
    for line in sys.stdin:
        if line.strip():
            q.put(line)
    q.put(None)

def run(make_bot: Any) -> None:
    q: 'queue.Queue[Optional[str]]' = queue.Queue()
    threading.Thread(target=_reader, args=(q,), daemon=True).start()
    bot = None
    last: Dict[str, Any] = {}
    errors = 0
    while True:
        batch = [q.get()]
        while True:
            try:
                batch.append(q.get_nowait())
            except queue.Empty:
                break
        latest = None
        for raw in batch:
            if raw is None:
                return
            try:
                msg = json.loads(raw)
            except ValueError:
                continue
            kind = msg.get('type')
            if kind == 'hello':
                draw.caps = msg.get('config', {}).get('debug', {})
                try:
                    bot = make_bot(msg)
                except Exception:
                    log(traceback.format_exc())
                name = getattr(bot, 'name', None) or getattr(make_bot, '__name__', 'bot')
                _send({'type': 'ready', 'name': str(name)[:32]})
            elif kind == 'obs':
                latest = msg
            elif kind == 'end':
                return
        if latest is None:
            continue
        action: Dict[str, Any] = {}
        draw.clear()
        if bot is not None:
            try:
                action = dict(bot.act(latest) or {})
                last = {k: action[k] for k in _CONTINUOUS if k in action}
            except Exception:
                errors += 1
                if errors <= 5 or errors % 100 == 0:
                    log(f"act() failed at tick {latest['tick']} ({errors} so far):\n{traceback.format_exc()}")
                action = dict(last)
        action['tick'] = latest['tick']
        d = draw.take()
        if d and 'debug' not in action:
            action['debug'] = d
        try:
            _send(action)
        except (TypeError, ValueError):
            log(f"unserializable action at tick {latest['tick']}:\n{traceback.format_exc()}")
            _send({'tick': latest['tick'], **last})
if __name__ == '__main__':
    log('library module')
