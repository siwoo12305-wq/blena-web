"""
BLACK ARENA TOWER — 웹 게임 서버 (완전 독립형)
봇과 같은 프로세스이면 active_games 직접 공유
봇 없이 단독 실행해도 game.html 서빙 + 전투 로직 내장
"""
import os, sys, random, copy, secrets
from fastapi import FastAPI, HTTPException
from fastapi.responses import FileResponse
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel

sys.path.insert(0, os.path.dirname(os.path.dirname(__file__)))

app = FastAPI()
app.add_middleware(CORSMiddleware, allow_origins=["*"], allow_methods=["*"], allow_headers=["*"])

HTML_PATH = os.path.join(os.path.dirname(__file__), "game.html")

# ── 토큰 관리 ─────────────────────────────────────────────────────
web_tokens: dict[str, int] = {}   # token → uid
_local_games: dict[int, dict] = {}  # 봇 없을 때 로컬 게임 저장
bot_ref = None

def set_bot(bot):
    global bot_ref
    bot_ref = bot

def generate_token(uid: int) -> str:
    t = secrets.token_urlsafe(16)
    web_tokens[t] = uid
    return t

def get_game_url(token: str, host="localhost", port=8080) -> str:
    return f"http://{host}:{port}/game?token={token}"

def _get_active_games():
    """봇 active_games 가져오기, 없으면 로컬 딕셔너리"""
    try:
        from cogs.blackarena import active_games
        return active_games
    except Exception:
        return _local_games

# ═══════════════════════════════════════════════════════════════
# 내장 게임 로직 (blackarena.py에서 복사 — 임포트 불필요)
# ═══════════════════════════════════════════════════════════════

ELEMENT_CHART = {
    "불":  {"물":0.75,"번개":1.0,"풀":1.5,"어둠":0.75,"얼음":1.5,"불":1.0,"물리":1.0},
    "물":  {"불":1.5,"번개":0.75,"풀":1.5,"어둠":1.0,"얼음":1.25,"물":1.0,"물리":1.0},
    "번개":{"불":1.0,"물":1.5,"풀":0.75,"어둠":1.0,"얼음":1.5,"번개":1.0,"물리":1.0},
    "풀":  {"불":0.75,"물":1.5,"번개":1.5,"어둠":0.75,"얼음":1.0,"풀":1.0,"물리":1.0},
    "어둠":{"불":1.5,"물":1.0,"번개":1.0,"풀":1.5,"얼음":1.5,"어둠":1.0,"물리":1.0},
    "얼음":{"불":0.75,"물":1.25,"번개":0.75,"풀":1.0,"어둠":0.75,"얼음":1.0,"물리":1.0},
    "물리":{"불":1.0,"물":1.0,"번개":1.0,"풀":1.0,"어둠":1.0,"얼음":1.0,"물리":1.0},
}

GRADE_SCALE = {
    "common":1.0,"uncommon":1.8,"rare":3.5,"legendary":6.0,"mythic":9.0,
    "spirit":13.0,"immortal":18.0,"celestial":25.0,"admin":40.0,
}

SHOP_POTION_POOL = ["HP포션","신비한포션","크리포션","마력포션","방무포션","명중포션"]
POTION_COSTS = {"HP포션":3,"신비한포션":8,"크리포션":4,"마력포션":4,"방무포션":5,"명중포션":3}

EVENT_LIST = [
    {"name":"메마른 샘물","emoji":"💧","effect":{"type":"heal_one","value":0.5},"weight":30},
    {"name":"무브의 은혜","emoji":"✨","effect":{"type":"heal_one","value":1.0},"weight":20},
    {"name":"축복의 샘물","emoji":"🌟","effect":{"type":"heal_all"},"weight":10},
    {"name":"자연의 단 한번","emoji":"🌿","effect":{"type":"revive_token"},"weight":15},
    {"name":"사랑의 날씨","emoji":"💕","effect":{"type":"dmg_reduce_buff","value":0.3,"duration":4},"weight":15},
    {"name":"피의 계약","emoji":"🩸","effect":{"type":"blood_pact","cost_hp":0.4,"value":0.5,"duration":4},"weight":10},
]

def _elem_mult(atk, def_):
    return ELEMENT_CHART.get(atk, {}).get(def_, 1.0)

def _calc_parry_rate(pet, mon):
    sp = pet.get("speed", 60)
    sm = mon.get("spd", mon.get("speed", 50))
    return min(65, int(sp / (sp + sm) * 100))

def _roll_event():
    weights = [e["weight"] for e in EVENT_LIST]
    return random.choices(EVENT_LIST, weights=weights, k=1)[0]

def _regen(game, pct=0.20):
    for p in game["party"]:
        if p["alive"]:
            p["hp"] = min(p["max_hp"], p["hp"] + int(p["max_hp"] * pct))
            p["cur_mana"] = min(p["max_mana"], p["cur_mana"] + int(p["max_mana"] * 0.25))

def _tick_pet(pet):
    for lst in ("buffs", "debuffs"):
        new = []
        for b in pet.get(lst, []):
            b["duration"] -= 1
            if b["duration"] >= 0:
                new.append(b)
        pet[lst] = new
    for k in list(pet.get("skill_cd", {}).keys()):
        pet["skill_cd"][k] -= 1
        if pet["skill_cd"][k] <= 0:
            del pet["skill_cd"][k]
    pet["cur_mana"] = min(pet["max_mana"], pet["cur_mana"] + int(pet["max_mana"] * 0.15))

def _tick_mon(mon):
    new = []
    for d in mon.get("debuffs", []):
        d["duration"] -= 1
        if d["duration"] >= 0:
            new.append(d)
    mon["debuffs"] = new

def _calc_dmg(pet, mon, skill_mult, ignore_def=0.0, is_crit=False):
    """blackarena.calc_dmg 그대로"""
    raw = pet.get("element_raw", pet["element"])
    if raw == "원소" or isinstance(raw, (list, tuple)):
        # 최적 속성 선택
        elems = raw if isinstance(raw, (list, tuple)) else ["불","물","번개","풀","어둠","얼음","물리"]
        best_elem = max(elems, key=lambda e: _elem_mult(e, mon.get("element","물리")))
        atk_elem = best_elem
    else:
        atk_elem = pet["element"]

    em = _elem_mult(atk_elem, mon.get("element", "물리"))
    eff_def = int(mon["def"] * (1 - ignore_def))

    atk = pet["atk"]
    for b in pet.get("buffs", []):
        if b["type"] == "atk_boost_pct":
            atk = int(atk * (1 + b["value"]))
        elif b["type"] == "opportunity":
            atk = int(atk * 1.30)

    mult_final = skill_mult * (1 + pet.get("bonus_skill", 0))
    base = max(1, int(atk * mult_final) - eff_def // 2)
    base = int(base * em)

    crit_chance = 0.10 + pet.get("bonus_crit", 0)
    crit = is_crit or random.random() < crit_chance
    if crit:
        base = int(base * 1.8)

    dmg = max(1, int(base * random.uniform(0.9, 1.1)))
    # ★ 한방 사망 방지: 최대 몬스터 HP의 60%
    dmg = min(dmg, int(mon["max_hp"] * 0.60))
    return dmg, crit, atk_elem

def _build_monster(floor: int, top_grade: str = "common") -> dict:
    """blackarena.build_monster 그대로"""
    try:
        from cogs.blackarena import build_monster
        return build_monster(floor, top_grade)
    except Exception:
        pass

    # 봇 없을 때 인라인 fallback
    try:
        from cogs.bat_data import NORMAL_MONSTERS, ELITE_MONSTERS, BOSS_MONSTERS, FIXED_BOSSES, HIDDEN_BOSSES
        floor_mult = 1.0 + (floor - 1) * 0.06
        grade_mult = GRADE_SCALE.get(top_grade, 1.0)
        mult = floor_mult * grade_mult

        if floor in HIDDEN_BOSSES:
            base = copy.deepcopy(HIDDEN_BOSSES[floor]); base["is_boss"] = True; mult *= 1.5
        elif floor in FIXED_BOSSES:
            base = copy.deepcopy(FIXED_BOSSES[floor]); base["is_boss"] = True; mult *= 1.5
        elif floor % 10 == 0:
            base = copy.deepcopy(random.choice(BOSS_MONSTERS)); base["is_boss"] = True; mult *= 1.5
        elif floor % 5 == 0:
            base = copy.deepcopy(random.choice(ELITE_MONSTERS)); base["is_boss"] = False; base["is_elite"] = True; mult *= 1.2
        else:
            base = copy.deepcopy(random.choice(NORMAL_MONSTERS)); base["is_boss"] = False; base["is_elite"] = False

        base.update({
            "max_hp": max(50, int(base["hp"] * mult)),
            "hp":     max(50, int(base["hp"] * mult)),
            "atk":    max(5,  int(base["atk"] * mult)),
            "def":    max(1,  int(base["def"] * mult)),
            "level":  min(10, (floor - 1) // 10),
            "buffs": [], "debuffs": [], "pattern_idx": 0,
        })
        if "pattern" not in base:
            base["pattern"] = ["기본 공격", "강타"]
        return base
    except Exception as e:
        # 최후 수단 더미 몬스터 (절대 1800 HP 아님)
        hp = max(80, int(100 * (1.0 + (floor-1)*0.06)))
        return {
            "name":"슬라임", "emoji":"🟢", "element":"풀",
            "hp":hp, "max_hp":hp, "atk":max(5,int(30*(1+(floor-1)*0.06))),
            "def":max(1,int(10*(1+(floor-1)*0.06))), "spd":10,
            "is_boss":False, "is_elite":False, "level":(floor-1)//10,
            "pattern":["기본 공격","강타"], "pattern_idx":0,
            "buffs":[], "debuffs":[], "silver":2, "gold":0,
        }

def _calc_reward(game):
    f = game["floor"] - 1
    bet = game["bet"]
    if f <= 0 or game["wins"] < 3:
        return 0
    return max(int(bet * 0.5), int(bet * (0.5 + (f / 30) * 3.0)))

def _gen_shop(game):
    items = []
    for p in random.sample(SHOP_POTION_POOL, min(3, len(SHOP_POTION_POOL))):
        items.append({"type": "potion", "name": p, "cost": POTION_COSTS.get(p, 3)})
    f = game["floor"]
    grade = "common" if f < 10 else ("rare" if f < 20 else "legendary")
    try:
        from cogs.bat_data import WEAPONS
        pool = [w for w, d in WEAPONS.items() if d.get("grade") == grade]
        if not pool:
            pool = list(WEAPONS.keys())
        for w in random.sample(pool, min(4, len(pool))):
            from cogs.bat_data import WEAPONS as WP
            d = dict(WP[w]); d["type"] = "weapon"; d["name"] = w
            items.append(d)
    except Exception:
        pass
    return items

def _apply_skill_effect(effect, pet, mon):
    logs = []
    if not effect:
        return logs
    et = effect.get("type", "")
    if et == "burn":
        mon["debuffs"].append({"type":"burn","value":0.05,"duration":3}); logs.append("🔥 화상!")
    elif et in ("poison","poison_dot"):
        mon["debuffs"].append({"type":"poison","value":0.04,"duration":4}); logs.append("☠️ 독!")
    elif et == "freeze":
        mon["debuffs"].append({"type":"freeze","value":0,"duration":2})
        mon["spd"] = max(1, int(mon.get("spd",50)*0.5)); logs.append("❄️ 빙결!")
    elif et == "stun":
        mon["debuffs"].append({"type":"stun","value":0,"duration":effect.get("duration",1)}); logs.append("⚡ 스턴!")
    elif et == "bleed":
        mon["debuffs"].append({"type":"bleed","value":0.04,"duration":3}); logs.append("🩸 출혈!")
    elif et == "shock":
        dmg = max(1, int(pet["atk"] * 0.5 - mon["def"] // 4))
        dmg = min(dmg, int(mon["max_hp"] * 0.60))
        mon["hp"] = max(0, mon["hp"] - dmg); logs.append(f"⚡ 감전! 연쇄 {dmg} 추가 피해!")
    elif et == "heal":
        h = int(pet["max_hp"] * effect.get("value", 20) / 100)
        pet["hp"] = min(pet["max_hp"], pet["hp"] + h); logs.append(f"💚 HP+{h}!")
    elif et == "armor_break":
        pass  # 이미 ignore_def에서 처리
    elif et == "buff_atk":
        pet["buffs"].append({"type":"atk_boost_pct","value":effect.get("value",0.3),"duration":effect.get("turns",2)})
    return logs

def _serialize(obj):
    if isinstance(obj, dict):
        return {k: _serialize(v) for k, v in obj.items()}
    if isinstance(obj, (list, tuple)):
        return [_serialize(i) for i in obj]
    if isinstance(obj, (int, float, str, bool)) or obj is None:
        return obj
    return str(obj)

# ═══════════════════════════════════════════════════════════════
# 공통 전투 처리 함수
# ═══════════════════════════════════════════════════════════════
async def _do_battle_action(game, uid, action, extra):
    """반환값: (response_dict, should_remove_game)"""
    active_games = _get_active_games()
    pet = game["party"][game["active"]]
    mon = game["monster"]
    logs = []
    pet_dmg = 0; mon_dmg = 0; pet_crit = False

    game["parry_available"] = False

    # ── 포션 ───────────────────────────────────────────────────
    if action == "potion":
        pname = extra.get("potion_name", "")
        if not pname or game["potions"].get(pname, 0) <= 0:
            raise HTTPException(400, "포션 없음")
        game["potions"][pname] -= 1
        if pname == "HP포션":
            h = int(pet["max_hp"] * 0.30); pet["hp"] = min(pet["max_hp"], pet["hp"] + h)
            logs.append(f"🧪 HP+{h}!")
        elif pname == "신비한포션":
            pet["hp"] = pet["max_hp"]; logs.append("✨ 완전 회복!")
        elif pname == "크리포션":
            pet["buffs"].append({"type":"crit_boost","value":1,"duration":1}); logs.append("⚗️ 크리 확정!")
        elif pname == "마력포션":
            pet["cur_mana"] = pet["max_mana"]; logs.append("💙 마나 충전!")
        elif pname == "방무포션":
            pet["buffs"].append({"type":"armor_break_buff","value":1.0,"duration":1}); logs.append("🔴 방무 확정!")
        elif pname == "명중포션":
            pet["buffs"].append({"type":"sure_hit","value":0,"duration":1}); logs.append("🎯 명중 확정!")
        return {"phase":"battle","game_state":_serialize(game),"logs":logs,
                "pet_dmg":0,"mon_dmg":0,"mon_anim_delay":0}

    # ── 교체 ───────────────────────────────────────────────────
    if action == "swap":
        idx = extra.get("swap_idx", -1)
        if idx < 0 or idx >= len(game["party"]) or not game["party"][idx]["alive"] or idx == game["active"]:
            raise HTTPException(400, "교체 불가")
        game["active"] = idx
        return {"phase":"battle","game_state":_serialize(game),
                "logs":[f"🔄 {game['party'][idx]['name']} 출전!"],"pet_dmg":0,"mon_dmg":0,"mon_anim_delay":0}

    # ── 포기 ───────────────────────────────────────────────────
    if action == "give_up":
        reward = _calc_reward(game) if game["wins"] >= 3 else 0
        gid = game.get("gid", 0)
        active_games.pop(uid, None)
        if bot_ref and reward:
            try:
                await bot_ref.db.update_coins(uid, gid, reward)
                await bot_ref.db.log_gamble(uid, gid, "블레나", game["bet"],
                                            f"포기 {game['floor']-1}층", reward - game["bet"])
            except Exception as e:
                print(f"[reward] {e}")
        return {"phase":"ended","reward":reward,"floor":game["floor"]-1,
                "msg":f"🏳️ 포기 — {'💰 '+str(reward)+'코인' if reward else '보상없음 (3승 미달)'}"}

    # ── 패링 ───────────────────────────────────────────────────
    if action == "parry":
        parry_dmg = game.pop("pending_parry_dmg", 0)
        pr = _calc_parry_rate(pet, mon)
        if random.randint(1, 100) <= pr:
            cd = max(1, int(pet["atk"] * 0.8) - mon["def"] // 2)
            cd = min(cd, int(mon["max_hp"] * 0.60))
            mon["hp"] = max(0, mon["hp"] - cd)
            pet["ult_gauge"] = min(7, pet["ult_gauge"] + 1)
            pet_dmg = cd
            logs.append(f"⚡ 패링 성공! ({pr}%) 반격 {cd}!")
            if mon["hp"] <= 0:
                return await _on_kill(game, uid, logs, pet_dmg, False, active_games)
        else:
            fd = int(parry_dmg * 1.3)
            pet["hp"] = max(0, pet["hp"] - fd)
            mon_dmg = fd
            logs.append(f"❌ 패링 실패! ({pr}%) 피해 {fd} (×1.3)")
            if pet["hp"] <= 0:
                return await _handle_pet_death(game, uid, pet, logs, pet_dmg, mon_dmg, False, active_games)
        _tick_pet(game["party"][game["active"]]); _tick_mon(mon)
        return {"phase":game["phase"],"game_state":_serialize(game),
                "logs":logs,"pet_dmg":pet_dmg,"mon_dmg":mon_dmg,"pet_crit":False,"mon_anim_delay":0}

    # ── 스킬 ───────────────────────────────────────────────────
    if action in ("skill1", "skill2", "ult", "secret"):
        skill = pet["skills"].get(action)
        if not skill:
            raise HTTPException(400, f"스킬 없음: {action}")
        mc = skill.get("mana", 10)
        if pet["cur_mana"] < mc:
            raise HTTPException(400, "마나 부족")
        if pet["skill_cd"].get(skill["name"], 0) > 0:
            raise HTTPException(400, "쿨타임")
        if action != "ult":
            pet["cur_mana"] -= mc

        ignore_def = 0.0
        effect = skill.get("effect")
        if effect and effect.get("type") == "armor_break":
            ignore_def = effect.get("value", 0.5)
        if any(b["type"] == "armor_break_buff" for b in pet.get("buffs", [])):
            ignore_def = max(ignore_def, 1.0)
        is_crit = any(b["type"] == "crit_boost" for b in pet.get("buffs", []))

        # 회피 체크
        evasion = sum(b["value"] for b in mon.get("buffs", []) if b["type"] == "evasion")
        if random.random() < evasion:
            logs.append(f"💨 {mon['name']} 회피!")
            pet_dmg = 0
        else:
            dmg, crit, _ = _calc_dmg(pet, mon, skill.get("dmg_mult", 1.0), ignore_def, is_crit)
            pet["buffs"] = [b for b in pet["buffs"]
                           if b["type"] not in ("crit_boost", "armor_break_buff", "opportunity")]
            mon["hp"] = max(0, mon["hp"] - dmg)
            pet_dmg = dmg; pet_crit = crit
            logs.append(f"⚔️ {skill['name']} → {dmg} 피해{'  💥크리!' if crit else ''}")

            if action == "ult":
                pet["ult_gauge"] = 0
            else:
                pet["ult_gauge"] = min(7, pet["ult_gauge"] + skill.get("ult_gain", 1))
            if skill.get("cooldown", 0) > 0:
                pet["skill_cd"][skill["name"]] = skill["cooldown"]
            if effect:
                logs += _apply_skill_effect(effect, pet, mon)

    elif action == "defend":
        pet["buffs"].append({"type":"dmg_reduce","value":0.35,"duration":1})
        logs.append("🛡️ 방어 태세! 피해 35% 감소")

    elif action == "opportunity":
        pet["buffs"].append({"type":"opportunity","value":0.30,"duration":1})
        logs.append("⚡ 기회 포착! 다음 공격 +30%")

    else:
        raise HTTPException(400, f"알 수 없는 액션: {action}")

    # ── 몬스터 처치 확인 ──────────────────────────────────────
    if mon["hp"] <= 0:
        return await _on_kill(game, uid, logs, pet_dmg, pet_crit, active_games)

    # ── 몬스터 도트 ───────────────────────────────────────────
    dot = 0
    for d in list(mon.get("debuffs", [])):
        if d["type"] == "burn":
            v = max(1, int(mon["hp"] * d["value"])); mon["hp"] = max(0, mon["hp"] - v); dot += v
        elif d["type"] in ("poison", "bleed"):
            v = max(1, int(mon["max_hp"] * d["value"])); mon["hp"] = max(0, mon["hp"] - v); dot += v
    if dot:
        logs.append(f"☠️ 지속 피해 {dot}!")
    if mon["hp"] <= 0:
        return await _on_kill(game, uid, logs, pet_dmg, pet_crit, active_games)

    # ── 몬스터 반격 ───────────────────────────────────────────
    stunned = any(d["type"] in ("stun","freeze") for d in mon.get("debuffs",[]))
    if stunned:
        logs.append(f"❄️ {mon['name']} 행동불가!")
    else:
        patterns = mon.get("pattern", ["기본 공격"])
        if isinstance(patterns, str):
            patterns = ["기본 공격", "강타"]
        pidx = mon.get("pattern_idx", 0) % len(patterns)
        pname = patterns[pidx]
        mon["pattern_idx"] = (pidx + 1) % len(patterns)

        def_val = sum(b["value"] for b in pet.get("buffs",[]) if b["type"] == "dmg_reduce")
        bm = 1.8 if mon.get("is_boss") else 1.0
        m_dmg = max(1, int(mon["atk"] * bm * random.uniform(0.85, 1.15)) - pet["def"] // 2)
        m_dmg = int(m_dmg * (1 - min(0.80, def_val)))

        spd_p = pet.get("speed", 60); spd_m = mon.get("spd", 50)
        evade = min(0.15, spd_p / (spd_p + spd_m) * 0.20)
        if random.random() < evade:
            logs.append(f"💨 {pet['name']} 회피!")
        elif mon.get("trait") == "insta_kill" and random.random() < 0.06:
            pet["hp"] = 0
            logs.append(f"💀 {mon['name']}의 즉사 공격!")
        else:
            pet["hp"] = max(0, pet["hp"] - m_dmg)
            mon_dmg = m_dmg
            logs.append(f"👹 {mon['name']}의 {pname} → {m_dmg} 피해!")
            game["parry_available"] = True
            game["pending_parry_dmg"] = m_dmg

    # ── 펫 사망 확인 ──────────────────────────────────────────
    if pet["hp"] <= 0:
        return await _handle_pet_death(game, uid, pet, logs, pet_dmg, mon_dmg, pet_crit, active_games)

    _tick_pet(game["party"][game["active"]]); _tick_mon(mon)
    return {
        "phase": game["phase"],
        "game_state": _serialize(game),
        "logs": logs,
        "pet_dmg": pet_dmg, "mon_dmg": mon_dmg, "pet_crit": pet_crit,
        "mon_anim_delay": 1000,   # ★ 내 공격 후 1초 뒤 몬스터 반격
    }

async def _on_kill(game, uid, logs, pet_dmg, pet_crit, active_games):
    mon = game["monster"]
    silver = mon.get("silver", mon.get("leaf", random.randint(1,3)))
    if isinstance(silver, int):
        pass
    else:
        silver = random.randint(1, 3)
    gold = mon.get("gold", mon.get("gold_leaf", 0))
    game["silver"] += silver; game["gold"] += gold; game["wins"] += 1
    logs.append(f"🏆 {mon['name']} 처치! 🍃+{silver}" + (f" 🌟+{gold}" if gold else ""))
    nf = game["floor"] + 1
    _regen(game, 0.15)
    _tick_pet(game["party"][game["active"]])

    # 이벤트층
    if game["floor"] % 10 == 9:
        evt = _roll_event()
        game["current_event"] = evt; game["phase"] = "event"; game["floor"] = nf
        return {"phase":"event","game_state":_serialize(game),"logs":logs,
                "event":evt,"pet_dmg":pet_dmg,"mon_dmg":0,"pet_crit":pet_crit,"mon_anim_delay":0}

    # 상점 (3승마다)
    if game["wins"] % 3 == 0:
        game["shop_items"] = _gen_shop(game); game["phase"] = "shop"; game["floor"] = nf
        return {"phase":"shop","game_state":_serialize(game),"logs":logs,
                "shop_items":game["shop_items"],"pet_dmg":pet_dmg,"mon_dmg":0,
                "pet_crit":pet_crit,"mon_anim_delay":0}

    # 결과 (3승 이상 달성)
    if game["wins"] >= 3:
        game["phase"] = "result"; game["floor"] = nf
        reward = _calc_reward(game)
        return {"phase":"result","game_state":_serialize(game),"logs":logs,
                "reward":reward,"pet_dmg":pet_dmg,"mon_dmg":0,"pet_crit":pet_crit,"mon_anim_delay":0}

    # 다음 층
    game["floor"] = nf
    game["monster"] = _build_monster(nf, game.get("top_grade","common"))
    game["phase"] = "battle"
    return {"phase":"battle","game_state":_serialize(game),
            "logs":logs+[f"➡️ {nf}층 진입!"],"pet_dmg":pet_dmg,"mon_dmg":0,
            "pet_crit":pet_crit,"mon_anim_delay":0}

async def _handle_pet_death(game, uid, pet, logs, pet_dmg, mon_dmg, pet_crit, active_games):
    pet["alive"] = False
    logs.append(f"💀 {pet['name']} 전투불능!")
    nxt = next((i for i,p in enumerate(game["party"]) if p["alive"] and i != game["active"]), None)
    if nxt is not None:
        game["active"] = nxt
        logs.append(f"🔄 {game['party'][nxt]['name']} 출전!")
        _tick_pet(game["party"][nxt]); _tick_mon(game["monster"])
        return {"phase":"battle","game_state":_serialize(game),
                "logs":logs,"pet_dmg":pet_dmg,"mon_dmg":mon_dmg,"pet_crit":pet_crit,"mon_anim_delay":0}
    # 부활 체크
    if any(p.get("has_revive") for p in game["party"]):
        for p in game["party"]:
            p["alive"] = True; p["hp"] = int(p["max_hp"] * 0.30); p["has_revive"] = False
        game["active"] = 0
        logs.append("🌟 부활! 전원 30% HP!")
        return {"phase":"battle","game_state":_serialize(game),
                "logs":logs,"pet_dmg":pet_dmg,"mon_dmg":mon_dmg,"pet_crit":pet_crit,"mon_anim_delay":0}
    # 전멸
    game["phase"] = "defeat"
    gid = game.get("gid", 0)
    active_games.pop(uid, None)
    if bot_ref:
        try:
            await bot_ref.db.log_gamble(uid, gid, "블레나", game["bet"],
                                        f"패배 {game['floor']}층", -game["bet"])
        except: pass
    return {"phase":"defeat","game_state":_serialize(game),
            "logs":logs,"pet_dmg":pet_dmg,"mon_dmg":mon_dmg,"pet_crit":pet_crit}

# ═══════════════════════════════════════════════════════════════
# HTTP 엔드포인트
# ═══════════════════════════════════════════════════════════════
@app.get("/")
@app.get("/game")
async def game_page():
    return FileResponse(HTML_PATH)

@app.get("/api/game/state")
async def get_state(token: str):
    uid = web_tokens.get(token)
    ag = _get_active_games()
    if not uid or uid not in ag:
        raise HTTPException(404, "게임 없음 — Discord에서 /블레나 시작 을 먼저 하세요.")
    return _serialize(ag[uid])

class ActionReq(BaseModel):
    token: str
    action: str
    extra: dict = {}

@app.post("/api/game/action")
async def do_action(req: ActionReq):
    uid = web_tokens.get(req.token)
    ag  = _get_active_games()
    if not uid or uid not in ag:
        raise HTTPException(404, "게임 없음")

    game   = ag[uid]
    action = req.action
    extra  = req.extra or {}

    # ─── 결과 화면 ───────────────────────────────────────────
    if game["phase"] == "result":
        if action == "end":
            reward = _calc_reward(game)
            gid = game.get("gid", 0)
            ag.pop(uid, None)
            if bot_ref and reward:
                try:
                    await bot_ref.db.update_coins(uid, gid, reward)
                    await bot_ref.db.log_gamble(uid, gid, "블레나", game["bet"],
                                                f"{game['floor']-1}층 클리어", reward - game["bet"])
                except Exception as e:
                    print(f"[reward] {e}")
            return {"phase":"ended","reward":reward,"floor":game["floor"]-1,
                    "msg":f"💰 {reward:,} 코인 획득!"}
        if action == "continue":
            game["monster"] = _build_monster(game["floor"], game.get("top_grade","common"))
            game["phase"] = "battle"; _regen(game, 0.20)
            return {"phase":"battle","game_state":_serialize(game),
                    "logs":[f"⚔️ {game['floor']}층 도전!"],"mon_anim_delay":0}
        raise HTTPException(400, "결과화면: end/continue만 가능")

    # ─── 상점 ────────────────────────────────────────────────
    if game["phase"] == "shop":
        if action == "skip_shop":
            game["phase"] = "battle"
            game["monster"] = _build_monster(game["floor"], game.get("top_grade","common"))
            _regen(game, 0.10)
            return {"phase":"battle","game_state":_serialize(game),
                    "logs":["⏭️ 상점 스킵"],"mon_anim_delay":0}
        if action == "buy":
            idx   = extra.get("idx", -1)
            items = game.get("shop_items", [])
            if idx < 0 or idx >= len(items):
                raise HTTPException(400, "잘못된 인덱스")
            item = items[idx]
            cost = item.get("cost", 0)
            if cost > 0 and game["silver"] < cost:
                raise HTTPException(400, f"은잎 부족 ({cost}🍃 필요)")
            game["silver"] -= cost; items.pop(idx)
            if item["type"] == "potion":
                game["potions"][item["name"]] = game["potions"].get(item["name"], 0) + 1
                msg = f"🧪 {item['name']} 구매!"
            else:
                p = game["party"][game["active"]]
                if len(p.get("weapons",[])) >= 5:
                    raise HTTPException(400, "무기 슬롯 가득")
                p.setdefault("weapons",[]).append({"name":item["name"]})
                msg = f"🗡️ {item['name']} 장착!"
            return {"phase":"shop","game_state":_serialize(game),"logs":[msg]}
        raise HTTPException(400, "상점: buy/skip_shop만 가능")

    # ─── 이벤트 ──────────────────────────────────────────────
    if game["phase"] == "event":
        if action == "event_accept":
            evt = game.get("current_event", {})
            effect = evt.get("effect", {}); et = effect.get("type","")
            p2 = game["party"][game["active"]]
            log = f"{evt.get('emoji','')} {evt.get('name','')} 수령!"
            if et == "heal_one":
                h = int(p2["max_hp"] * effect.get("value",0.5))
                p2["hp"] = min(p2["max_hp"], p2["hp"] + h); log += f" HP+{h}"
            elif et == "heal_all":
                for p in game["party"]:
                    if p["alive"]: p["hp"] = p["max_hp"]
                log += " 전체 완전 회복!"
            elif et == "revive_token":
                for p in game["party"]:
                    if p["alive"]: p["has_revive"] = True
                log += " 부활권!"
            elif et == "dmg_reduce_buff":
                for p in game["party"]:
                    if p["alive"]: p["buffs"].append({"type":"dmg_reduce","value":effect.get("value",0.3),
                                                       "duration":effect.get("duration",4)})
                log += f" 피해감소 {effect.get('duration',4)}턴!"
            elif et == "blood_pact":
                for p in game["party"]:
                    if p["alive"]:
                        p["hp"] = max(1, int(p["hp"]*(1-effect.get("cost_hp",0.4))))
                        p["buffs"].append({"type":"atk_boost_pct","value":effect.get("value",0.5),
                                           "duration":effect.get("duration",4)})
                log += " 체력 희생→공격 증가!"
            game["phase"] = "battle"
            game["monster"] = _build_monster(game["floor"], game.get("top_grade","common"))
            game["current_event"] = None
            return {"phase":"battle","game_state":_serialize(game),"logs":[log],"mon_anim_delay":0}
        raise HTTPException(400, "이벤트: event_accept만 가능")

    # ─── 전투 ────────────────────────────────────────────────
    if game["phase"] != "battle":
        raise HTTPException(400, f"전투 중 아님 (phase={game['phase']})")

    return await _do_battle_action(game, uid, action, extra)

class SessionReq(BaseModel):
    uid: int
    game_state: dict

@app.post("/api/session/create")
async def create_session(req: SessionReq):
    """blackarena._sync_render 에서 호출 — 게임 상태 동기화"""
    ag = _get_active_games()
    ag[req.uid] = req.game_state
    # 토큰이 없으면 새로 발급
    existing = next((t for t,u in web_tokens.items() if u == req.uid), None)
    if not existing:
        existing = generate_token(req.uid)
    return {"ok": True, "token": existing}

if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host="0.0.0.0", port=8080)
