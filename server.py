"""
BLACK ARENA TOWER 웹게임 서버
Render.com에서 실행
"""
from fastapi import FastAPI, HTTPException
from fastapi.responses import HTMLResponse, FileResponse
from fastapi.staticfiles import StaticFiles
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel
import secrets, os, json, asyncio, aiohttp
from typing import Optional

app = FastAPI()

# CORS 허용 (봇 서버에서 호출 가능하게)
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_methods=["*"],
    allow_headers=["*"],
)

# 게임 세션 저장 (메모리)
game_sessions: dict[str, dict] = {}  # token → game_state

BOT_API_URL = os.getenv("BOT_API_URL", "")  # 봇 서버 URL

# ── 페이지 서빙 ──────────────────────────────────────────────────
@app.get("/")
async def root():
    return FileResponse("game.html")

@app.get("/game")
async def game():
    return FileResponse("game.html")

# ── 봇에서 게임 세션 생성 ─────────────────────────────────────────
class CreateSession(BaseModel):
    uid: int
    game_state: dict

@app.post("/api/session/create")
async def create_session(data: CreateSession):
    """봇이 호출 — 게임 세션 생성 후 토큰 반환"""
    token = secrets.token_urlsafe(16)
    game_sessions[token] = {
        "uid": data.uid,
        "state": data.game_state,
    }
    return {"token": token, "url": f"/game?token={token}"}

# ── 게임 상태 조회 ────────────────────────────────────────────────
@app.get("/api/game/state")
async def get_state(token: str):
    session = game_sessions.get(token)
    if not session:
        raise HTTPException(status_code=404, detail="세션 없음")
    return session["state"]

# ── 행동 처리 ────────────────────────────────────────────────────
class ActionRequest(BaseModel):
    token: str
    action: str
    skill_key: Optional[str] = None

@app.post("/api/game/action")
async def do_action(req: ActionRequest):
    session = game_sessions.get(req.token)
    if not session:
        raise HTTPException(status_code=404, detail="세션 없음")

    game = session["state"]

    # 봇 서버에 행동 전달
    if BOT_API_URL:
        try:
            async with aiohttp.ClientSession() as s:
                r = await s.post(f"{BOT_API_URL}/api/action", json={
                    "uid": session["uid"],
                    "action": req.action,
                    "skill_key": req.skill_key,
                }, timeout=aiohttp.ClientTimeout(total=10))
                if r.status == 200:
                    result = await r.json()
                    session["state"] = result.get("game_state", game)
                    return result
        except Exception as e:
            print(f"[bot api] {e}")

    # 봇 서버 없으면 로컬 처리 (테스트용)
    result = _local_process(game, req.action, req.skill_key)
    session["state"] = result["game_state"]
    return result

def _local_process(game, action, skill_key=None):
    """로컬 전투 처리 (봇 서버 없을 때)"""
    import random, math
    pet = game["party"][game["active"]]
    mon = game["monster"]
    logs = []
    pet_dmg = 0; pet_crit = False; mon_dmg = 0

    if action in ("skill1","skill2","ult","secret"):
        key = action if action != "action" else (skill_key or "skill1")
        skill = pet["skills"].get(key) or pet["skills"]["skill1"]
        mc = skill.get("mana", 10)
        pet["cur_mana"] = max(0, pet["cur_mana"] - mc)
        raw = max(1, int(pet["atk"] * skill.get("dmg_mult",1.4)) - mon["def"]//2)
        pet_crit = random.random() < 0.10
        pet_dmg  = int(raw * 1.8) if pet_crit else raw
        pet_dmg  = int(pet_dmg * random.uniform(0.9,1.1))
        mon["hp"] = max(0, mon["hp"] - pet_dmg)
        pet["ult_gauge"] = min(7, pet["ult_gauge"] + skill.get("ult_gain",1))
        logs.append(f"⚔️ {skill['name']} → {pet_dmg} 피해{'  💥크리!' if pet_crit else ''}")

    elif action == "defend":
        pet.setdefault("buffs",[]).append({"type":"dmg_reduce","value":0.35,"duration":1})
        logs.append("🛡️ 방어 태세!")

    elif action == "opportunity":
        pet.setdefault("buffs",[]).append({"type":"opportunity","value":0.30,"duration":1})
        logs.append("⚡ 기회 포착!")

    elif action == "parry":
        spd_p = pet.get("speed",60); spd_m = mon.get("spd",50)
        rate = min(65, int(spd_p/(spd_p+spd_m)*100))
        if random.randint(1,100) <= rate:
            cd = max(1, int(pet["atk"]*0.8) - mon["def"]//2)
            mon["hp"] = max(0, mon["hp"]-cd)
            logs.append(f"⚡ 패링 성공! 피해 0 + 반격 {cd}!")
        else:
            pdmg = game.get("pending_parry_dmg",0)
            fd = int(pdmg*1.3)
            pet["hp"] = max(0, pet["hp"]-fd)
            logs.append(f"❌ 패링 실패! {fd} 피해 (1.3배)")
        game["parry_available"] = False

    # 몬스터 처치
    if mon["hp"] <= 0:
        game["wins"] += 1
        silver = random.randint(1,3)
        game["silver"] += silver
        logs.append(f"🏆 {mon['name']} 처치! 🍃+{silver}")
        if game["wins"] >= 3:
            game["phase"] = "result"
        else:
            game["floor"] += 1
            game["monster"] = _make_mon(game["floor"], game.get("top_grade","common"))
        game["parry_available"] = False
        return {"game_state":game,"logs":logs,"pet_dmg":pet_dmg,"pet_crit":pet_crit,"mon_dmg":0}

    # 몬스터 공격
    if action not in ("parry",):
        patterns = mon.get("pattern",["기본 공격"])
        pname = patterns[mon.get("pattern_idx",0) % len(patterns)]
        mon["pattern_idx"] = (mon.get("pattern_idx",0)+1) % len(patterns)
        def_val = sum(b["value"] for b in pet.get("buffs",[]) if b["type"]=="dmg_reduce")
        bm = 1.8 if mon.get("is_boss") else 1.0
        m_dmg = max(1, int(mon["atk"]*bm*random.uniform(0.85,1.15)) - pet["def"]//2)
        m_dmg = int(m_dmg*(1-min(0.8,def_val)))
        pet["hp"] = max(0, pet["hp"]-m_dmg)
        mon_dmg = m_dmg
        logs.append(f"👹 {mon['name']}의 {pname} → {m_dmg} 피해!")
        game["parry_available"] = True
        game["pending_parry_dmg"] = m_dmg

    # 버프 감소, 마나 회복
    pet["buffs"] = [b for b in pet.get("buffs",[]) if b.get("duration",0)>1]
    for b in pet.get("buffs",[]): b["duration"] -= 1
    pet["cur_mana"] = min(pet["max_mana"], pet["cur_mana"]+int(pet["max_mana"]*0.15))

    # 펫 사망
    if pet["hp"] <= 0:
        pet["alive"] = False
        nxt = next((i for i,p in enumerate(game["party"]) if p["alive"] and i!=game["active"]), None)
        if nxt is not None:
            game["active"] = nxt
            logs.append(f"🔄 {game['party'][nxt]['name']} 출전!")
        else:
            game["phase"] = "defeat"

    return {"game_state":game,"logs":logs,"pet_dmg":pet_dmg,"pet_crit":pet_crit,"mon_dmg":mon_dmg}

def _make_mon(floor, top_grade="common"):
    import random
    SCALE = {"common":1.0,"uncommon":1.8,"rare":3.5,"legendary":6.0,"mythic":9.0}
    mult = (1+(floor-1)*0.06) * SCALE.get(top_grade,1.0)
    elems = ["불","물","번개","풀","어둠","얼음","물리"]
    elem  = random.choice(elems)
    names = {"불":["불꽃 도깨비","용암 벌레"],"물":["심연 물고기","늪지 개구리"],
             "번개":["번개 도마뱀","폭풍 벌"],"풀":["독버섯 생명체","부식 벌"],
             "어둠":["붉은 박쥐","타락한 사슴"],"얼음":["얼음 여우","서리 골렘"],
             "물리":["철갑 거북","광산 드워프"]}
    name = random.choice(names.get(elem,["미지의 몬스터"]))
    hp   = int(120*mult); atk = int(35*mult); def_ = int(10*mult)
    return {"name":name,"emoji":"👹","element":elem,
            "hp":hp,"max_hp":hp,"atk":atk,"def":def_,"spd":50,
            "is_boss":False,"is_elite":False,"level":min(10,(floor-1)//10),
            "pattern":["기본 공격","강타"],"pattern_idx":0,"buffs":[],"debuffs":[]}

# ── 게임 결과 → 봇에 전달 ─────────────────────────────────────────
class ResultRequest(BaseModel):
    token: str
    result: str  # "end" / "give_up"

@app.post("/api/game/result")
async def send_result(req: ResultRequest):
    session = game_sessions.get(req.token)
    if not session: raise HTTPException(404, "세션 없음")
    game = session["state"]

    if BOT_API_URL:
        try:
            async with aiohttp.ClientSession() as s:
                await s.post(f"{BOT_API_URL}/api/result", json={
                    "uid": session["uid"],
                    "floor": game["floor"],
                    "wins": game["wins"],
                    "silver": game["silver"],
                    "gold": game["gold"],
                    "result": req.result,
                })
        except Exception as e:
            print(f"[result] {e}")

    del game_sessions[req.token]
    return {"ok": True}

if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host="0.0.0.0", port=int(os.getenv("PORT",8080)))
