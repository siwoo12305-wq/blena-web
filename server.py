"""
BLACK ARENA TOWER — 웹 게임 API 서버
blackarena.py의 active_games + 전투 로직 직접 연결
"""
from fastapi import FastAPI, HTTPException
from fastapi.responses import FileResponse
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel
import secrets, os, sys, random

sys.path.insert(0, os.path.dirname(os.path.dirname(__file__)))

app = FastAPI()
app.add_middleware(CORSMiddleware, allow_origins=["*"], allow_methods=["*"], allow_headers=["*"])

web_tokens: dict[str, int] = {}
bot_ref = None

def set_bot(bot):
    global bot_ref
    bot_ref = bot

def generate_token(uid: int) -> str:
    token = secrets.token_urlsafe(16)
    web_tokens[token] = uid
    return token

def get_game_url(token: str, host: str = "localhost", port: int = 8080) -> str:
    return f"http://{host}:{port}/game?token={token}"

HTML_PATH = os.path.join(os.path.dirname(__file__), "game.html")

@app.get("/")
@app.get("/game")
async def game_page():
    return FileResponse(HTML_PATH)

@app.get("/api/game/state")
async def get_state(token: str):
    try:
        from cogs.blackarena import active_games
    except ImportError:
        raise HTTPException(503, "봇 미연결")
    uid = web_tokens.get(token)
    if not uid or uid not in active_games:
        raise HTTPException(404, "게임 없음")
    return _serialize(active_games[uid])

class ActionReq(BaseModel):
    token: str
    action: str
    extra: dict = {}

@app.post("/api/game/action")
async def do_action(req: ActionReq):
    try:
        from cogs.blackarena import active_games, build_monster, calc_reward, _regen, _gen_shop, calc_dmg, _roll_event, _calc_parry_rate
    except ImportError as e:
        raise HTTPException(503, f"봇 미연결: {e}")

    uid = web_tokens.get(req.token)
    if not uid or uid not in active_games:
        raise HTTPException(404, "게임 없음")

    game   = active_games[uid]
    action = req.action
    extra  = req.extra or {}
    pet    = game["party"][game["active"]]
    mon    = game["monster"]
    logs   = []
    pet_dmg = 0; mon_dmg = 0; pet_crit = False

    # ═══ 결과 화면 ═══════════════════════════════════════════
    if game["phase"] == "result":
        if action == "end":
            reward = calc_reward(game)
            gid = game.get("gid", 0)
            active_games.pop(uid, None)
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
            game["monster"] = build_monster(game["floor"], game.get("top_grade","common"))
            game["phase"] = "battle"; _regen(game, 0.20)
            return {"phase":"battle","game_state":_serialize(game),
                    "logs":[f"⚔️ {game['floor']}층 도전!"],"mon_anim_delay":0}
        raise HTTPException(400, "결과 화면: end/continue만 가능")

    # ═══ 상점 ════════════════════════════════════════════════
    if game["phase"] == "shop":
        if action == "skip_shop":
            game["phase"] = "battle"
            game["monster"] = build_monster(game["floor"], game.get("top_grade","common"))
            _regen(game, 0.10)
            return {"phase":"battle","game_state":_serialize(game),
                    "logs":["⏭️ 상점 스킵"],"mon_anim_delay":0}
        if action == "buy":
            idx   = extra.get("idx", -1)
            items = game.get("shop_items", [])
            if idx < 0 or idx >= len(items): raise HTTPException(400, "잘못된 인덱스")
            item = items[idx]
            cost = item.get("cost", 0)
            if cost > 0 and game["silver"] < cost:
                raise HTTPException(400, f"은잎 부족({cost})")
            game["silver"] -= cost; items.pop(idx)
            if item["type"] == "potion":
                game["potions"][item["name"]] = game["potions"].get(item["name"], 0) + 1
                msg = f"🧪 {item['name']} 구매!"
            else:
                p2 = game["party"][game["active"]]
                if len(p2.get("weapons",[])) >= 5: raise HTTPException(400, "무기 슬롯 가득")
                p2.setdefault("weapons",[]).append({"name":item["name"]})
                msg = f"🗡️ {item['name']} 장착!"
            return {"phase":"shop","game_state":_serialize(game),"logs":[msg]}
        raise HTTPException(400, "상점: buy/skip_shop만 가능")

    # ═══ 이벤트 ══════════════════════════════════════════════
    if game["phase"] == "event":
        if action == "event_accept":
            evt = game.get("current_event", {})
            effect = evt.get("effect", {}); et = effect.get("type","")
            p2 = game["party"][game["active"]]
            log = f"{evt.get('emoji','')} {evt.get('name','')} 수령!"
            if et == "heal_one":
                h = int(p2["max_hp"] * effect.get("value",0.5))
                p2["hp"] = min(p2["max_hp"], p2["hp"]+h); log += f" HP+{h}"
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
            game["monster"] = build_monster(game["floor"], game.get("top_grade","common"))
            game["current_event"] = None
            return {"phase":"battle","game_state":_serialize(game),"logs":[log],"mon_anim_delay":0}
        raise HTTPException(400, "이벤트: event_accept만 가능")

    # ═══ 전투 ════════════════════════════════════════════════
    if game["phase"] != "battle":
        raise HTTPException(400, f"전투 중 아님 (phase={game['phase']})")

    game["parry_available"] = False

    # ── 포션 ──────────────────────────────────────────────
    if action == "potion":
        pname = extra.get("potion_name","")
        if not pname or game["potions"].get(pname,0) <= 0:
            raise HTTPException(400,"포션 없음")
        game["potions"][pname] -= 1
        if pname == "HP포션":
            h = int(pet["max_hp"]*0.30); pet["hp"] = min(pet["max_hp"], pet["hp"]+h)
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

    # ── 교체 ──────────────────────────────────────────────
    if action == "swap":
        idx = extra.get("swap_idx",-1)
        if idx < 0 or idx >= len(game["party"]) or not game["party"][idx]["alive"] or idx == game["active"]:
            raise HTTPException(400,"교체 불가")
        game["active"] = idx
        return {"phase":"battle","game_state":_serialize(game),
                "logs":[f"🔄 {game['party'][idx]['name']} 출전!"],"pet_dmg":0,"mon_dmg":0,"mon_anim_delay":0}

    # ── 포기 ──────────────────────────────────────────────
    if action == "give_up":
        reward = calc_reward(game) if game["wins"] >= 3 else 0
        gid = game.get("gid",0); active_games.pop(uid, None)
        if bot_ref and reward:
            try:
                await bot_ref.db.update_coins(uid, gid, reward)
                await bot_ref.db.log_gamble(uid, gid, "블레나", game["bet"],
                                            f"포기 {game['floor']-1}층", reward-game["bet"])
            except: pass
        return {"phase":"ended","reward":reward,"floor":game["floor"]-1,
                "msg":f"🏳️ 포기 — {'💰 '+str(reward)+'코인' if reward else '보상없음(3승 미달)'}"}

    # ── 스킬/방어/기회 ────────────────────────────────────
    if action in ("skill1","skill2","ult","secret"):
        skill = pet["skills"].get(action)
        if not skill: raise HTTPException(400,f"스킬 없음: {action}")
        mc = skill.get("mana",10)
        if pet["cur_mana"] < mc: raise HTTPException(400,"마나 부족")
        if pet["skill_cd"].get(skill["name"],0) > 0: raise HTTPException(400,"쿨타임")
        if action != "ult": pet["cur_mana"] -= mc

        ignore_def = 0.0
        effect = skill.get("effect")
        if effect and effect.get("type") == "armor_break": ignore_def = effect.get("value",0.5)
        if any(b["type"]=="armor_break_buff" for b in pet.get("buffs",[])): ignore_def = max(ignore_def,1.0)
        is_crit = any(b["type"]=="crit_boost" for b in pet.get("buffs",[]))

        dmg, crit, _ = calc_dmg(pet, mon, skill.get("dmg_mult",1.0), ignore_def, is_crit)
        # ★ 한방 사망 방지: 최대 몬스터 HP의 60%
        dmg = min(dmg, int(mon["max_hp"] * 0.60))

        pet["buffs"] = [b for b in pet["buffs"] if b["type"] not in ("crit_boost","armor_break_buff","opportunity")]
        mon["hp"] = max(0, mon["hp"] - dmg)
        pet_dmg = dmg; pet_crit = crit
        logs.append(f"⚔️ **{skill['name']}** → **{dmg}** 피해{'  💥크리!' if crit else ''}")

        if action == "ult": pet["ult_gauge"] = 0
        else: pet["ult_gauge"] = min(7, pet["ult_gauge"] + skill.get("ult_gain",1))
        if skill.get("cooldown",0) > 0: pet["skill_cd"][skill["name"]] = skill["cooldown"]
        if effect: logs += _apply_skill_effect(effect, pet, mon)

    elif action == "defend":
        pet["buffs"].append({"type":"dmg_reduce","value":0.35,"duration":1})
        logs.append("🛡️ 방어 태세! 피해 35% 감소")

    elif action == "opportunity":
        pet["buffs"].append({"type":"opportunity","value":0.30,"duration":1})
        logs.append("⚡ 기회 포착! 다음 공격 +30%")

    elif action == "parry":
        parry_dmg = game.pop("pending_parry_dmg", 0)
        pr = _calc_parry_rate(pet, mon)
        if random.randint(1,100) <= pr:
            cd = max(1, int(pet["atk"]*0.8) - mon["def"]//2)
            mon["hp"] = max(0, mon["hp"]-cd); pet_dmg = cd
            pet["ult_gauge"] = min(7, pet["ult_gauge"]+1)
            logs.append(f"⚡ **패링 성공!** 반격 {cd}!")
        else:
            fd = int(parry_dmg*1.3); pet["hp"] = max(0, pet["hp"]-fd); mon_dmg = fd
            logs.append(f"❌ **패링 실패!** 피해 {fd}(×1.3)")
            if pet["hp"] <= 0:
                pet["alive"] = False
                nxt = next((i for i,p in enumerate(game["party"]) if p["alive"] and i!=game["active"]), None)
                if nxt is None:
                    game["phase"] = "defeat"; active_games.pop(uid, None)
                    if bot_ref:
                        try: await bot_ref.db.log_gamble(uid, game.get("gid",0), "블레나",
                                                         game["bet"], f"패배 {game['floor']}층", -game["bet"])
                        except: pass
                    return {"phase":"defeat","game_state":_serialize(game),"logs":logs,
                            "pet_dmg":0,"mon_dmg":mon_dmg,"pet_crit":False,"mon_anim_delay":0}
                game["active"] = nxt
                logs.append(f"🔄 {game['party'][nxt]['name']} 출전!")
        _tick(game["party"][game["active"]]); _tick_mon(mon)
        return {"phase":game["phase"],"game_state":_serialize(game),
                "logs":logs,"pet_dmg":pet_dmg,"mon_dmg":mon_dmg,"pet_crit":pet_crit,"mon_anim_delay":0}
    else:
        raise HTTPException(400, f"알 수 없는 액션: {action}")

    # ── 몬스터 처치 확인 ──────────────────────────────────
    if mon["hp"] <= 0:
        return await _on_kill(game, uid, logs, pet_dmg, pet_crit, active_games,
                              build_monster, calc_reward, _regen, _gen_shop, _roll_event)

    # ── 몬스터 도트 ───────────────────────────────────────
    dot = 0
    for d in list(mon.get("debuffs",[])):
        if d["type"]=="burn":
            v=max(1,int(mon["hp"]*d["value"])); mon["hp"]=max(0,mon["hp"]-v); dot+=v
        elif d["type"] in ("poison","bleed"):
            v=max(1,int(mon["max_hp"]*d["value"])); mon["hp"]=max(0,mon["hp"]-v); dot+=v
    if dot: logs.append(f"☠️ 지속피해 {dot}!")
    if mon["hp"] <= 0:
        return await _on_kill(game, uid, logs, pet_dmg, pet_crit, active_games,
                              build_monster, calc_reward, _regen, _gen_shop, _roll_event)

    # ── 몬스터 반격 ───────────────────────────────────────
    stunned = any(d["type"] in ("stun","freeze") for d in mon.get("debuffs",[]))
    if stunned:
        logs.append(f"❄️ {mon['name']} 행동불가!")
    else:
        patterns = mon.get("pattern",["기본 공격"])
        pidx = mon.get("pattern_idx",0) % len(patterns)
        pname = patterns[pidx]; mon["pattern_idx"] = (pidx+1) % len(patterns)
        def_val = sum(b["value"] for b in pet.get("buffs",[]) if b["type"]=="dmg_reduce")
        bm = 1.8 if mon.get("is_boss") else 1.0
        m_dmg = max(1, int(mon["atk"]*bm*random.uniform(0.85,1.15)) - pet["def"]//2)
        m_dmg = int(m_dmg*(1-min(0.80,def_val)))
        spd_p = pet.get("speed",60); spd_m = mon.get("spd",50)
        evade = min(0.15, spd_p/(spd_p+spd_m)*0.20)
        if random.random() < evade:
            logs.append(f"💨 {pet['name']} 회피!")
        elif mon.get("trait")=="insta_kill" and random.random()<0.06:
            pet["hp"] = 0; logs.append(f"💀 {mon['name']}의 즉사 공격!")
        else:
            pet["hp"] = max(0, pet["hp"]-m_dmg); mon_dmg = m_dmg
            logs.append(f"👹 **{mon['name']}의 {pname}** → **{m_dmg}** 피해!")
            game["parry_available"] = True; game["pending_parry_dmg"] = m_dmg

    # ── 펫 사망 확인 ──────────────────────────────────────
    if pet["hp"] <= 0:
        pet["alive"] = False; logs.append(f"💀 {pet['name']} 전투불능!")
        nxt = next((i for i,p in enumerate(game["party"]) if p["alive"] and i!=game["active"]), None)
        if nxt is not None:
            game["active"] = nxt; logs.append(f"🔄 {game['party'][nxt]['name']} 출전!")
        else:
            has_revive = any(p.get("has_revive") for p in game["party"])
            if has_revive:
                for p in game["party"]:
                    p["alive"]=True; p["hp"]=int(p["max_hp"]*0.30); p["has_revive"]=False
                game["active"]=0; logs.append("🌟 부활! 전원 30% HP!")
            else:
                game["phase"] = "defeat"; active_games.pop(uid, None)
                if bot_ref:
                    try: await bot_ref.db.log_gamble(uid, game.get("gid",0), "블레나",
                                                     game["bet"], f"패배 {game['floor']}층", -game["bet"])
                    except: pass
                return {"phase":"defeat","game_state":_serialize(game),"logs":logs,
                        "pet_dmg":pet_dmg,"mon_dmg":mon_dmg,"pet_crit":pet_crit}

    _tick(game["party"][game["active"]]); _tick_mon(mon)
    return {"phase":game["phase"],"game_state":_serialize(game),
            "logs":logs,"pet_dmg":pet_dmg,"mon_dmg":mon_dmg,"pet_crit":pet_crit,
            "mon_anim_delay":1000}   # ★ 내 공격 후 1초 뒤 몬스터 반격 애니


# ── 헬퍼 ──────────────────────────────────────────────────────────
def _tick(pet):
    pet["buffs"]   = [b for b in pet.get("buffs",[])   if (b.__setitem__("duration",b["duration"]-1) or True) and b["duration"]>=0]
    pet["debuffs"] = [d for d in pet.get("debuffs",[]) if (d.__setitem__("duration",d["duration"]-1) or True) and d["duration"]>=0]
    for k in list(pet.get("skill_cd",{}).keys()):
        pet["skill_cd"][k] -= 1
        if pet["skill_cd"][k] <= 0: del pet["skill_cd"][k]
    pet["cur_mana"] = min(pet["max_mana"], pet["cur_mana"]+int(pet["max_mana"]*0.15))

def _tick_mon(mon):
    mon["debuffs"] = [d for d in mon.get("debuffs",[]) if (d.__setitem__("duration",d["duration"]-1) or True) and d["duration"]>=0]

def _apply_skill_effect(effect, pet, mon) -> list:
    logs=[]; et=effect.get("type","")
    if et=="burn":
        mon["debuffs"].append({"type":"burn","value":0.05,"duration":3}); logs.append("🔥 화상!")
    elif et in ("poison","poison_dot"):
        mon["debuffs"].append({"type":"poison","value":0.04,"duration":4}); logs.append("☠️ 독!")
    elif et=="freeze":
        mon["debuffs"].append({"type":"freeze","value":0,"duration":2})
        mon["spd"]=max(1,int(mon.get("spd",50)*0.5)); logs.append("❄️ 빙결!")
    elif et=="stun":
        mon["debuffs"].append({"type":"stun","value":0,"duration":effect.get("duration",1)}); logs.append("⚡ 스턴!")
    elif et=="bleed":
        mon["debuffs"].append({"type":"bleed","value":0.04,"duration":3}); logs.append("🩸 출혈!")
    elif et=="heal":
        h=int(pet["max_hp"]*effect.get("value",20)/100)
        pet["hp"]=min(pet["max_hp"],pet["hp"]+h); logs.append(f"💚 HP+{h}!")
    elif et=="buff_atk":
        pet["buffs"].append({"type":"atk_boost_pct","value":effect.get("value",0.3),"duration":effect.get("turns",2)})
    return logs

async def _on_kill(game, uid, logs, pet_dmg, pet_crit, active_games,
                   build_monster, calc_reward, _regen, _gen_shop, _roll_event):
    mon=game["monster"]
    silver=mon.get("silver",random.randint(1,3)); gold=mon.get("gold",0)
    game["silver"]+=silver; game["gold"]+=gold; game["wins"]+=1
    logs.append(f"🏆 **{mon['name']}** 처치! 🍃+{silver}" + (f" 🌟+{gold}" if gold else ""))
    nf=game["floor"]+1; _regen(game,0.15); _tick(game["party"][game["active"]])

    if game["floor"] % 10 == 9:
        evt=_roll_event(); game["current_event"]=evt; game["phase"]="event"; game["floor"]=nf
        return {"phase":"event","game_state":_serialize(game),"logs":logs,
                "event":evt,"pet_dmg":pet_dmg,"mon_dmg":0,"pet_crit":pet_crit,"mon_anim_delay":0}

    if game["wins"] % 3 == 0:
        game["shop_items"]=_gen_shop(game); game["phase"]="shop"; game["floor"]=nf
        return {"phase":"shop","game_state":_serialize(game),"logs":logs,
                "shop_items":game["shop_items"],"pet_dmg":pet_dmg,"mon_dmg":0,
                "pet_crit":pet_crit,"mon_anim_delay":0}

    if game["wins"] >= 3:
        game["phase"]="result"; game["floor"]=nf; reward=calc_reward(game)
        return {"phase":"result","game_state":_serialize(game),"logs":logs,
                "reward":reward,"pet_dmg":pet_dmg,"mon_dmg":0,"pet_crit":pet_crit,"mon_anim_delay":0}

    game["floor"]=nf; game["monster"]=build_monster(nf, game.get("top_grade","common")); game["phase"]="battle"
    return {"phase":"battle","game_state":_serialize(game),
            "logs":logs+[f"➡️ {nf}층 진입!"],"pet_dmg":pet_dmg,"mon_dmg":0,
            "pet_crit":pet_crit,"mon_anim_delay":0}

def _serialize(game):
    def fix(o):
        if isinstance(o, dict): return {k:fix(v) for k,v in o.items()}
        if isinstance(o, (list,tuple)): return [fix(i) for i in o]
        if isinstance(o, (int,float,str,bool)) or o is None: return o
        return str(o)
    return fix(game)

class SessionReq(BaseModel):
    uid: int
    game_state: dict

@app.post("/api/session/create")
async def create_session(req: SessionReq):
    """
    blackarena.py의 PetSelectView에서 호출.
    토큰 생성 후 반환 -> blackarena.py가 웹 링크에 붙여 유저에게 전달.
    유저가 game.html?token=XXX 접속 시 토큰으로 게임 상태 조회.
    """
    token = generate_token(req.uid)
    try:
        from cogs.blackarena import active_games
        active_games[req.uid] = req.game_state
    except:
        pass
    return {"ok": True, "token": token}

if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host="0.0.0.0", port=8080)
