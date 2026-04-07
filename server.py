"""BLACK ARENA TOWER — 웹 서버"""
import os,sys,random,copy,secrets
from fastapi import FastAPI,HTTPException
from fastapi.responses import FileResponse
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel
sys.path.insert(0,os.path.dirname(os.path.dirname(__file__)))
app=FastAPI()
app.add_middleware(CORSMiddleware,allow_origins=["*"],allow_methods=["*"],allow_headers=["*"])
HTML_PATH=os.path.join(os.path.dirname(__file__),"game.html")
_tokens:dict[str,int]={}
_local:dict[int,dict]={}
bot_ref=None
def set_bot(b): global bot_ref; bot_ref=b
def generate_token(uid:int)->str:
    t=secrets.token_urlsafe(16); _tokens[t]=uid; return t
def get_game_url(token,host="localhost",port=8080): return f"http://{host}:{port}/game?token={token}"
def _ag():
    try:
        from cogs.blackarena import active_games; return active_games
    except: return _local
ELEM={"불":{"물":0.75,"번개":1.0,"풀":1.5,"어둠":0.75,"얼음":1.5,"불":1.0,"물리":1.0},"물":{"불":1.5,"번개":0.75,"풀":1.5,"어둠":1.0,"얼음":1.25,"물":1.0,"물리":1.0},"번개":{"불":1.0,"물":1.5,"풀":0.75,"어둠":1.0,"얼음":1.5,"번개":1.0,"물리":1.0},"풀":{"불":0.75,"물":1.5,"번개":1.5,"어둠":0.75,"얼음":1.0,"풀":1.0,"물리":1.0},"어둠":{"불":1.5,"물":1.0,"번개":1.0,"풀":1.5,"얼음":1.5,"어둠":1.0,"물리":1.0},"얼음":{"불":0.75,"물":1.25,"번개":0.75,"풀":1.0,"어둠":0.75,"얼음":1.0,"물리":1.0},"물리":{"불":1.0,"물":1.0,"번개":1.0,"풀":1.0,"어둠":1.0,"얼음":1.0,"물리":1.0}}
GSCALE={"common":1.0,"uncommon":1.8,"rare":3.5,"legendary":6.0,"mythic":9.0,"spirit":13.0,"immortal":18.0,"celestial":25.0,"admin":40.0}
PCOST={"HP포션":3,"신비한포션":8,"크리포션":4,"마력포션":4,"방무포션":5,"명중포션":3}
SPOOL=["HP포션","신비한포션","크리포션","마력포션","방무포션","명중포션"]
EVTS=[{"name":"메마른 샘물","emoji":"💧","desc":"HP 50% 회복","effect":{"type":"heal_one","value":0.5},"weight":30},{"name":"무브의 은혜","emoji":"✨","desc":"HP 완전 회복","effect":{"type":"heal_one","value":1.0},"weight":20},{"name":"축복의 샘물","emoji":"🌟","desc":"전체 완전 회복","effect":{"type":"heal_all"},"weight":10},{"name":"자연의 단 한번","emoji":"🌿","desc":"부활권","effect":{"type":"revive_token"},"weight":15},{"name":"사랑의 날씨","emoji":"💕","desc":"피해 30% 감소 4턴","effect":{"type":"dmg_reduce_buff","value":0.3,"duration":4},"weight":15},{"name":"피의 계약","emoji":"🩸","desc":"HP-40% / 공격+50% 4턴","effect":{"type":"blood_pact","cost_hp":0.4,"value":0.5,"duration":4},"weight":10}]
def _em(a,d): return ELEM.get(a,{}).get(d,1.0)
def _pr(pet,mon): return min(65,int(pet.get("speed",60)/(pet.get("speed",60)+mon.get("spd",50))*100))
def _rev(): return random.choices(EVTS,weights=[e["weight"] for e in EVTS],k=1)[0]
def _regen(g,p):
    for x in g["party"]:
        if x["alive"]: x["hp"]=min(x["max_hp"],x["hp"]+int(x["max_hp"]*p)); x["cur_mana"]=min(x["max_mana"],x["cur_mana"]+int(x["max_mana"]*0.25))
def _tick(pet):
    for lst in("buffs","debuffs"):
        kept=[]
        for b in pet.get(lst,[]):
            b["duration"]-=1
            if b["duration"]>=0: kept.append(b)
        pet[lst]=kept
    for k in list(pet.get("skill_cd",{}).keys()):
        pet["skill_cd"][k]-=1
        if pet["skill_cd"][k]<=0: del pet["skill_cd"][k]
    pet["cur_mana"]=min(pet["max_mana"],pet["cur_mana"]+int(pet["max_mana"]*0.15))
def _tick_mon(mon):
    kept=[]
    for d in mon.get("debuffs",[]):
        d["duration"]-=1
        if d["duration"]>=0: kept.append(d)
    mon["debuffs"]=kept
# bat_data 미리 로드 (discord 없이도 동작)
_NM=_EM_=_BM=_FB=_HB=None
def _load_mon_data():
    global _NM,_EM_,_BM,_FB,_HB
    if _NM is not None: return True
    try:
        import sys as _sys
        # discord mock — bat_data가 간접적으로 helpers를 import할 수 있음
        if "discord" not in _sys.modules:
            from unittest.mock import MagicMock
            _sys.modules["discord"]=MagicMock()
            _sys.modules["discord.ext"]=MagicMock()
            _sys.modules["discord.ext.commands"]=MagicMock()
        from cogs.bat_data import NORMAL_MONSTERS,ELITE_MONSTERS,BOSS_MONSTERS,FIXED_BOSSES,HIDDEN_BOSSES
        _NM=NORMAL_MONSTERS; _EM_=ELITE_MONSTERS; _BM=BOSS_MONSTERS; _FB=FIXED_BOSSES; _HB=HIDDEN_BOSSES
        return True
    except Exception as e:
        print(f"[mon_data] {e}"); return False

def _mon(floor,tg="common"):
    # 1순위: blackarena.build_monster (봇 실행 중일 때)
    try:
        from cogs.blackarena import build_monster; return build_monster(floor,tg)
    except: pass
    # 2순위: bat_data 직접 사용
    if _load_mon_data():
        try:
            m=(1+(floor-1)*0.06)*GSCALE.get(tg,1.0)
            if floor in _HB: b=copy.deepcopy(_HB[floor]); b["is_boss"]=True; m*=1.5
            elif floor in _FB: b=copy.deepcopy(_FB[floor]); b["is_boss"]=True; m*=1.5
            elif floor%10==0: b=copy.deepcopy(random.choice(_BM)); b["is_boss"]=True; b["is_elite"]=False; m*=1.5
            elif floor%5==0: b=copy.deepcopy(random.choice(_EM_)); b["is_boss"]=False; b["is_elite"]=True; m*=1.2
            else: b=copy.deepcopy(random.choice(_NM)); b["is_boss"]=False; b["is_elite"]=False
            b.update({"max_hp":max(50,int(b["hp"]*m)),"hp":max(50,int(b["hp"]*m)),"atk":max(5,int(b["atk"]*m)),"def":max(1,int(b["def"]*m)),"level":min(10,(floor-1)//10),"buffs":[],"debuffs":[],"pattern_idx":0})
            if "pattern" not in b: b["pattern"]=["기본 공격","강타"]
            if "silver" not in b: b["silver"]=random.randint(1,3)
            if "gold" not in b: b["gold"]=1 if b.get("is_boss") else 0
            return b
        except Exception as e:
            print(f"[_mon] {e}")
    # 폴백: 랜덤 속성 일반 몬스터
    elems=["불","물","번개","풀","어둠","얼음","물리"]
    names=["붉은 박쥐","철갑 거북","사막 전갈","폐허 고블린","얼음 여우","용암 벌레","어둠 고양이","모래 도적"]
    hp=max(80,int(100*(1+(floor-1)*0.06))*GSCALE.get(tg,1.0))
    return {"name":random.choice(names),"emoji":"👹","element":random.choice(elems),"hp":int(hp),"max_hp":int(hp),"atk":max(5,int(30*(1+(floor-1)*0.06))),"def":max(1,int(10*(1+(floor-1)*0.06))),"spd":10,"is_boss":False,"is_elite":False,"level":(floor-1)//10,"pattern":["기본 공격","강타"],"pattern_idx":0,"buffs":[],"debuffs":[],"silver":random.randint(1,3),"gold":0}
def _shop(g):
    items=[]
    for p in random.sample(SPOOL,min(3,len(SPOOL))): items.append({"type":"potion","name":p,"cost":PCOST.get(p,3)})
    f=g["floor"]; grade="common" if f<10 else("rare" if f<20 else "legendary")
    try:
        from cogs.bat_data import WEAPONS
        pool=[w for w,d in WEAPONS.items() if d.get("grade")==grade] or list(WEAPONS.keys())
        for w in random.sample(pool,min(4,len(pool))): d2=dict(WEAPONS[w]); d2["type"]="weapon"; d2["name"]=w; items.append(d2)
    except: pass
    return items
def _reward(g):
    f=g["floor"]-1; bet=g["bet"]
    if f<=0 or g["wins"]<3: return 0
    return max(int(bet*0.5),int(bet*(0.5+(f/30)*3.0)))
def _dmg(pet,mon,sm,ign=0.0,ic=False):
    raw=pet.get("element_raw",pet["element"])
    if raw=="원소" or isinstance(raw,(list,tuple)):
        el=raw if isinstance(raw,(list,tuple)) else ["불","물","번개","풀","어둠","얼음","물리"]
        ae=max(el,key=lambda e:_em(e,mon.get("element","물리")))
    else: ae=pet["element"]
    em=_em(ae,mon.get("element","물리")); eff=int(mon["def"]*(1-ign))
    atk=pet["atk"]
    for b in pet.get("buffs",[]):
        if b["type"]=="atk_boost_pct": atk=int(atk*(1+b["value"]))
        elif b["type"]=="opportunity": atk=int(atk*1.30)
    base=max(1,int(atk*sm*(1+pet.get("bonus_skill",0)))-eff//2); base=int(base*em)
    crit=ic or random.random()<0.10+pet.get("bonus_crit",0)
    if crit: base=int(base*1.8)
    return min(max(1,int(base*random.uniform(0.9,1.1))),int(mon["max_hp"]*0.60)),crit,ae
def _eff(eff,pet,mon):
    if not eff: return []
    logs=[]; et=eff.get("type","")
    if et=="burn": mon["debuffs"].append({"type":"burn","value":0.05,"duration":3}); logs.append("🔥 화상!")
    elif et in("poison","poison_dot"): mon["debuffs"].append({"type":"poison","value":0.04,"duration":4}); logs.append("☠️ 독!")
    elif et=="freeze": mon["debuffs"].append({"type":"freeze","value":0,"duration":2}); mon["spd"]=max(1,int(mon.get("spd",50)*0.5)); logs.append("❄️ 빙결!")
    elif et=="stun": mon["debuffs"].append({"type":"stun","value":0,"duration":eff.get("duration",1)}); logs.append("⚡ 스턴!")
    elif et=="bleed": mon["debuffs"].append({"type":"bleed","value":0.04,"duration":3}); logs.append("🩸 출혈!")
    elif et=="heal": h=int(pet["max_hp"]*eff.get("value",20)/100); pet["hp"]=min(pet["max_hp"],pet["hp"]+h); logs.append(f"💚 HP+{h}!")
    elif et=="buff_atk": pet["buffs"].append({"type":"atk_boost_pct","value":eff.get("value",0.3),"duration":eff.get("turns",2)})
    return logs
def _s(o):
    if isinstance(o,dict): return {k:_s(v) for k,v in o.items()}
    if isinstance(o,(list,tuple)): return [_s(i) for i in o]
    if isinstance(o,(int,float,str,bool)) or o is None: return o
    return str(o)
async def _kill(g,uid,logs,pd,pc,ag):
    mon=g["monster"]; sv=mon.get("silver",mon.get("leaf",random.randint(1,3))); sv=sv if isinstance(sv,int) else random.randint(1,3)
    gd=mon.get("gold",mon.get("gold_leaf",0)); g["silver"]+=sv; g["gold"]+=gd; g["wins"]+=1
    logs.append(f"🏆 {mon['name']} 처치! 🍃+{sv}"+(f" 🌟+{gd}" if gd else ""))
    nf=g["floor"]+1; _regen(g,0.15); _tick(g["party"][g["active"]])
    if g["floor"]%10==9:
        evt=_rev(); g["current_event"]=evt; g["phase"]="event"; g["floor"]=nf
        return {"phase":"event","game_state":_s(g),"logs":logs,"event":evt,"pet_dmg":pd,"mon_dmg":0,"pet_crit":pc,"mon_anim_delay":0}
    # blackarena.py 원본 순서: wins>=3 먼저, 그 다음 wins%3==0
    if g["wins"]>=3:
        g["phase"]="result"; g["floor"]=nf
        return {"phase":"result","game_state":_s(g),"logs":logs,"reward":_reward(g),"pet_dmg":pd,"mon_dmg":0,"pet_crit":pc,"mon_anim_delay":0}
    if g["wins"]%3==0:
        g["shop_items"]=_shop(g); g["phase"]="shop"; g["floor"]=nf
        return {"phase":"shop","game_state":_s(g),"logs":logs,"shop_items":g["shop_items"],"pet_dmg":pd,"mon_dmg":0,"pet_crit":pc,"mon_anim_delay":0}
    g["floor"]=nf; g["monster"]=_mon(nf,g.get("top_grade","common")); g["phase"]="battle"
    return {"phase":"battle","game_state":_s(g),"logs":logs+[f"➡️ {nf}층!"],"pet_dmg":pd,"mon_dmg":0,"pet_crit":pc,"mon_anim_delay":0}
async def _dead(g,uid,pet,logs,pd,md,pc,ag):
    pet["alive"]=False; logs.append(f"💀 {pet['name']} 전투불능!")
    nxt=next((i for i,p in enumerate(g["party"]) if p["alive"] and i!=g["active"]),None)
    if nxt is not None:
        g["active"]=nxt; logs.append(f"🔄 {g['party'][nxt]['name']} 출전!")
        _tick(g["party"][nxt]); _tick_mon(g["monster"])
        return {"phase":"battle","game_state":_s(g),"logs":logs,"pet_dmg":pd,"mon_dmg":md,"pet_crit":pc,"mon_anim_delay":0}
    if any(p.get("has_revive") for p in g["party"]):
        for p in g["party"]: p["alive"]=True; p["hp"]=int(p["max_hp"]*0.30); p["has_revive"]=False
        g["active"]=0; logs.append("🌟 부활!")
        return {"phase":"battle","game_state":_s(g),"logs":logs,"pet_dmg":pd,"mon_dmg":md,"pet_crit":pc,"mon_anim_delay":0}
    g["phase"]="defeat"; ag.pop(uid,None)
    if bot_ref:
        try: await bot_ref.db.log_gamble(uid,g.get("gid",0),"블레나",g["bet"],f"패배 {g['floor']}층",-g["bet"])
        except: pass
    return {"phase":"defeat","game_state":_s(g),"logs":logs,"pet_dmg":pd,"mon_dmg":md,"pet_crit":pc}
async def _battle(g,uid,a,ex):
    ag=_ag(); pet=g["party"][g["active"]]; mon=g["monster"]
    logs=[]; pd=0; md=0; pc=False; g["parry_available"]=False
    if a=="potion":
        pn=ex.get("potion_name","")
        if not pn or g["potions"].get(pn,0)<=0: raise HTTPException(400,"포션 없음")
        g["potions"][pn]-=1
        if pn=="HP포션": h=int(pet["max_hp"]*0.30); pet["hp"]=min(pet["max_hp"],pet["hp"]+h); logs.append(f"🧪 HP+{h}!")
        elif pn=="신비한포션": pet["hp"]=pet["max_hp"]; logs.append("✨ 완전 회복!")
        elif pn=="크리포션": pet["buffs"].append({"type":"crit_boost","value":1,"duration":1}); logs.append("⚗️ 크리!")
        elif pn=="마력포션": pet["cur_mana"]=pet["max_mana"]; logs.append("💙 마나 충전!")
        elif pn=="방무포션": pet["buffs"].append({"type":"armor_break_buff","value":1.0,"duration":1}); logs.append("🔴 방무!")
        elif pn=="명중포션": pet["buffs"].append({"type":"sure_hit","value":0,"duration":1}); logs.append("🎯 명중!")
        return {"phase":"battle","game_state":_s(g),"logs":logs,"pet_dmg":0,"mon_dmg":0,"mon_anim_delay":0}
    if a=="swap":
        idx=ex.get("swap_idx",-1)
        if idx<0 or idx>=len(g["party"]) or not g["party"][idx]["alive"] or idx==g["active"]: raise HTTPException(400,"교체 불가")
        g["active"]=idx; return {"phase":"battle","game_state":_s(g),"logs":[f"🔄 {g['party'][idx]['name']} 출전!"],"pet_dmg":0,"mon_dmg":0,"mon_anim_delay":0}
    if a=="give_up":
        reward=_reward(g) if g["wins"]>=3 else 0; ag.pop(uid,None)
        if bot_ref and reward:
            try: await bot_ref.db.update_yak(uid,g.get("gid",0),reward); await bot_ref.db.log_gamble(uid,g.get("gid",0),"블레나",g["bet"],f"포기",reward-g["bet"])
            except: pass
        return {"phase":"ended","reward":reward,"floor":g["floor"]-1,"msg":f"🏳️ {'💰 '+str(reward)+'코인' if reward else '보상없음'}"}
    if a=="parry":
        ppd=g.pop("pending_parry_dmg",0); pr=_pr(pet,mon)
        if random.randint(1,100)<=pr:
            cd=min(max(1,int(pet["atk"]*0.8)-mon["def"]//2),int(mon["max_hp"]*0.60))
            mon["hp"]=max(0,mon["hp"]-cd); pd=cd; pet["ult_gauge"]=min(7,pet["ult_gauge"]+1)
            logs.append(f"⚡ 패링 성공! 반격 {cd}!")
            if mon["hp"]<=0: return await _kill(g,uid,logs,cd,False,ag)
        else:
            fd=int(ppd*1.3); pet["hp"]=max(0,pet["hp"]-fd); md=fd
            logs.append(f"❌ 패링 실패! -{fd}")
            if pet["hp"]<=0: return await _dead(g,uid,pet,logs,0,fd,False,ag)
        _tick(g["party"][g["active"]]); _tick_mon(mon)
        return {"phase":g["phase"],"game_state":_s(g),"logs":logs,"pet_dmg":pd,"mon_dmg":md,"pet_crit":False,"mon_anim_delay":0}
    if a in("skill1","skill2","ult","secret"):
        sk=pet["skills"].get(a)
        if not sk: raise HTTPException(400,f"스킬없음:{a}")
        mc=sk.get("mana",10)
        if pet["cur_mana"]<mc: raise HTTPException(400,"마나 부족")
        if pet["skill_cd"].get(sk["name"],0)>0: raise HTTPException(400,"쿨타임")
        if a!="ult": pet["cur_mana"]-=mc
        ign=0.0; ef=sk.get("effect")
        if ef and ef.get("type")=="armor_break": ign=ef.get("value",0.5)
        if any(b["type"]=="armor_break_buff" for b in pet.get("buffs",[])): ign=max(ign,1.0)
        ic=any(b["type"]=="crit_boost" for b in pet.get("buffs",[]))
        ev=sum(b["value"] for b in mon.get("buffs",[]) if b["type"]=="evasion")
        if random.random()<ev: logs.append(f"💨 {mon['name']} 회피!")
        else:
            dm,cr,_=_dmg(pet,mon,sk.get("dmg_mult",1.0),ign,ic)
            pet["buffs"]=[b for b in pet["buffs"] if b["type"] not in("crit_boost","armor_break_buff","opportunity")]
            mon["hp"]=max(0,mon["hp"]-dm); pd=dm; pc=cr
            logs.append(f"⚔️ {sk['name']} → {dm}{'  💥크리!' if cr else ''}")
        if a=="ult": pet["ult_gauge"]=0
        else: pet["ult_gauge"]=min(7,pet["ult_gauge"]+sk.get("ult_gain",1))
        if sk.get("cooldown",0)>0: pet["skill_cd"][sk["name"]]=sk["cooldown"]
        if ef: logs+=_eff(ef,pet,mon)
    elif a=="defend": pet["buffs"].append({"type":"dmg_reduce","value":0.35,"duration":1}); logs.append("🛡️ 방어!")
    elif a=="opportunity": pet["buffs"].append({"type":"opportunity","value":0.30,"duration":1}); logs.append("⚡ 기회!")
    else: raise HTTPException(400,f"알수없음:{a}")
    if mon["hp"]<=0: return await _kill(g,uid,logs,pd,pc,ag)
    dot=0
    for d in list(mon.get("debuffs",[])):
        if d["type"]=="burn": v=max(1,int(mon["hp"]*d["value"])); mon["hp"]=max(0,mon["hp"]-v); dot+=v
        elif d["type"] in("poison","bleed"): v=max(1,int(mon["max_hp"]*d["value"])); mon["hp"]=max(0,mon["hp"]-v); dot+=v
    if dot: logs.append(f"☠️ 도트 {dot}!")
    if mon["hp"]<=0: return await _kill(g,uid,logs,pd,pc,ag)
    stunned=any(d["type"] in("stun","freeze") for d in mon.get("debuffs",[]))
    if stunned: logs.append(f"❄️ {mon['name']} 행동불가!")
    else:
        pats=mon.get("pattern",["기본 공격"]); pats=pats if isinstance(pats,list) else ["기본 공격"]
        pi=mon.get("pattern_idx",0)%len(pats); pn=pats[pi]; mon["pattern_idx"]=(pi+1)%len(pats)
        dv=sum(b["value"] for b in pet.get("buffs",[]) if b["type"]=="dmg_reduce")
        bm=1.8 if mon.get("is_boss") else 1.0
        md2=max(1,int(mon["atk"]*bm*random.uniform(0.85,1.15))-pet["def"]//2); md2=int(md2*(1-min(0.80,dv)))
        sp=pet.get("speed",60); sm=mon.get("spd",50)
        if random.random()<min(0.15,sp/(sp+sm)*0.20): logs.append(f"💨 {pet['name']} 회피!")
        elif mon.get("trait")=="insta_kill" and random.random()<0.06: pet["hp"]=0; logs.append("💀 즉사!")
        else:
            pet["hp"]=max(0,pet["hp"]-md2); md=md2
            logs.append(f"👹 {mon['name']}의 {pn} → {md2}!")
            g["parry_available"]=True; g["pending_parry_dmg"]=md2
    if pet["hp"]<=0: return await _dead(g,uid,pet,logs,pd,md,pc,ag)
    _tick(g["party"][g["active"]]); _tick_mon(mon)
    return {"phase":g["phase"],"game_state":_s(g),"logs":logs,"pet_dmg":pd,"mon_dmg":md,"pet_crit":pc,"mon_anim_delay":1000}

@app.get("/")
@app.get("/game")
async def page(): return FileResponse(HTML_PATH)
@app.get("/api/game/state")
async def state(token:str):
    uid=_tokens.get(token); ag=_ag()
    if not uid or uid not in ag: raise HTTPException(404,"게임 없음 — Discord에서 /블레나 시작 하세요.")
    return _s(ag[uid])
class R(BaseModel):
    token:str; action:str; extra:dict={}
@app.post("/api/game/action")
async def action(req:R):
    uid=_tokens.get(req.token); ag=_ag()
    if not uid or uid not in ag: raise HTTPException(404,"게임 없음")
    g=ag[uid]; a=req.action; ex=req.extra or {}
    if g["phase"]=="result":
        if a=="end":
            rw=_reward(g); ag.pop(uid,None)
            if bot_ref and rw:
                try: await bot_ref.db.update_yak(uid,g.get("gid",0),rw); await bot_ref.db.log_gamble(uid,g.get("gid",0),"블레나",g["bet"],f"{g['floor']-1}층",rw-g["bet"])
                except: pass
            return {"phase":"ended","reward":rw,"floor":g["floor"]-1,"msg":f"💰 {rw:,}코인!"}
        if a=="continue":
            g["monster"]=_mon(g["floor"],g.get("top_grade","common")); g["phase"]="battle"; _regen(g,0.20)
            return {"phase":"battle","game_state":_s(g),"logs":[f"⚔️ {g['floor']}층 도전!"],"mon_anim_delay":0}
        raise HTTPException(400,"result: end/continue")
    if g["phase"]=="shop":
        if a=="skip_shop":
            g["phase"]="battle"; g["shop_items"]=[]; g["monster"]=_mon(g["floor"],g.get("top_grade","common")); _regen(g,0.10)
            return {"phase":"battle","game_state":_s(g),"logs":["⏭️ 스킵"],"mon_anim_delay":0}
        if a=="buy":
            idx=ex.get("idx",-1); items=g.get("shop_items",[])
            if idx<0 or idx>=len(items): raise HTTPException(400,"잘못된 인덱스")
            item=items[idx]; cost=item.get("cost",0)
            if cost>0 and g["silver"]<cost: raise HTTPException(400,f"은잎 부족({cost}🍃)")
            g["silver"]-=cost; items.pop(idx)
            if item["type"]=="potion": g["potions"][item["name"]]=g["potions"].get(item["name"],0)+1; msg=f"🧪 {item['name']}!"
            else:
                p=g["party"][g["active"]]
                if len(p.get("weapons",[]))>=5: raise HTTPException(400,"무기 슬롯 가득")
                p.setdefault("weapons",[]).append({"name":item["name"]}); msg=f"🗡️ {item['name']}!"
            return {"phase":"shop","game_state":_s(g),"logs":[msg]}
        raise HTTPException(400,"shop: buy/skip_shop")
    if g["phase"]=="event":
        if a=="event_accept":
            evt=g.get("current_event",{}); ef=evt.get("effect",{}); et=ef.get("type","")
            p2=g["party"][g["active"]]; log=f"{evt.get('emoji','')} {evt.get('name','')} 수령!"
            if et=="heal_one": h=int(p2["max_hp"]*ef.get("value",0.5)); p2["hp"]=min(p2["max_hp"],p2["hp"]+h); log+=f" HP+{h}"
            elif et=="heal_all":
                for p in g["party"]:
                    if p["alive"]: p["hp"]=p["max_hp"]
                log+=" 전체 완전 회복!"
            elif et=="revive_token":
                for p in g["party"]:
                    if p["alive"]: p["has_revive"]=True
                log+=" 부활권!"
            elif et=="dmg_reduce_buff":
                for p in g["party"]:
                    if p["alive"]: p["buffs"].append({"type":"dmg_reduce","value":ef.get("value",0.3),"duration":ef.get("duration",4)})
                log+=f" 피해감소!"
            elif et=="blood_pact":
                for p in g["party"]:
                    if p["alive"]: p["hp"]=max(1,int(p["hp"]*(1-ef.get("cost_hp",0.4)))); p["buffs"].append({"type":"atk_boost_pct","value":ef.get("value",0.5),"duration":ef.get("duration",4)})
                log+=" 공격 증가!"
            g["phase"]="battle"; g["current_event"]=None; g["monster"]=_mon(g["floor"],g.get("top_grade","common"))
            return {"phase":"battle","game_state":_s(g),"logs":[log],"mon_anim_delay":0}
        raise HTTPException(400,"event: event_accept")
    if g["phase"]!="battle": raise HTTPException(400,f"phase={g['phase']}")
    return await _battle(g,uid,a,ex)
class SR(BaseModel):
    uid:int; game_state:dict
@app.post("/api/session/create")
async def session(req:SR):
    ag=_ag(); ag[req.uid]=req.game_state
    t=next((k for k,v in _tokens.items() if v==req.uid),None)
    if not t: t=generate_token(req.uid)
    return {"ok":True,"token":t}
if __name__=="__main__":
    import uvicorn; uvicorn.run(app,host="0.0.0.0",port=8080)
