import json
from pathlib import Path

import pytest
from app.services import game_forge
from app.services.game_forge import extract_html, extract_json, validate_game

GAME = """<!doctype html>
<html><head><meta charset="utf-8"><title>Orbit</title>
<style>html,body{margin:0;background:#050b16;overflow:hidden}canvas{display:block}</style></head>
<body><canvas id="c"></canvas><script>
const cv=document.getElementById('c'),ctx=cv.getContext('2d');
function resize(){cv.width=innerWidth;cv.height=innerHeight;}resize();addEventListener('resize',resize);
const ship={x:200,y:200,r:12,speed:220};
const keys={};addEventListener('keydown',e=>{keys[e.key]=true;e.preventDefault();});
addEventListener('keyup',e=>{keys[e.key]=false;});
addEventListener('pointerdown',e=>{ship.x=e.clientX;ship.y=e.clientY;});
let rocks=[],score=0,spawn=0,over=false,best=0,last=0;
function reset(){rocks=[];score=0;spawn=0;over=false;}
function update(dt){
  if(keys.ArrowLeft)ship.x-=ship.speed*dt;
  if(keys.ArrowRight)ship.x+=ship.speed*dt;
  if(keys.ArrowUp)ship.y-=ship.speed*dt;
  if(keys.ArrowDown)ship.y+=ship.speed*dt;
  ship.x=Math.max(ship.r,Math.min(cv.width-ship.r,ship.x));
  ship.y=Math.max(ship.r,Math.min(cv.height-ship.r,ship.y));
  score+=dt*10;
  spawn-=dt;
  const rate=Math.max(0.18,0.7-score/900);
  if(spawn<=0){spawn=rate;rocks.push({x:Math.random()*cv.width,y:-30,r:10+Math.random()*22,v:80+Math.random()*140+score/4});}
  for(const r of rocks){
    r.y+=r.v*dt;
    const d=Math.hypot(r.x-ship.x,r.y-ship.y);
    if(d<r.r+ship.r){over=true;best=Math.max(best,Math.floor(score));}
  }
  rocks=rocks.filter(r=>r.y<cv.height+60);
}
function draw(){
  ctx.fillStyle='#050b16';ctx.fillRect(0,0,cv.width,cv.height);
  ctx.fillStyle='#41c3f1';
  for(const r of rocks){ctx.beginPath();ctx.arc(r.x,r.y,r.r,0,7);ctx.fill();}
  ctx.fillStyle='#dff6ff';
  ctx.beginPath();ctx.moveTo(ship.x,ship.y-ship.r);ctx.lineTo(ship.x+ship.r,ship.y+ship.r);
  ctx.lineTo(ship.x-ship.r,ship.y+ship.r);ctx.closePath();ctx.fill();
  ctx.fillStyle='#f79f43';ctx.font='20px monospace';
  ctx.fillText('SCORE '+Math.floor(score),24,40);
  ctx.fillText('BEST '+best,24,68);
  if(over){
    ctx.fillStyle='rgba(5,11,22,0.82)';ctx.fillRect(0,0,cv.width,cv.height);
    ctx.fillStyle='#dff6ff';ctx.font='48px monospace';ctx.textAlign='center';
    ctx.fillText('GAME OVER',cv.width/2,cv.height/2-20);
    ctx.fillStyle='#41c3f1';ctx.font='22px monospace';
    ctx.fillText('click or press space to restart',cv.width/2,cv.height/2+40);
    ctx.textAlign='left';
  }
}
function loop(t){
  const dt=Math.min(0.05,(t-last)/1000||0);last=t;
  if(over){if(keys[' ']||keys.Enter)reset();}else update(dt);
  draw();requestAnimationFrame(loop);
}
addEventListener('keydown',e=>{if(over&&(e.key===' '||e.key==='Enter'))reset();});
requestAnimationFrame(loop);
</script></body></html>"""


class FakeGateway:
    def __init__(self, spec=None, code=GAME):
        self.spec = spec or {"title": "Orbit Runner", "genre": "arcade", "hook": "dodge",
                             "loop": "dodge rocks", "mechanics": ["move"], "controls": {"ArrowUp": "up"},
                             "win": "survive", "fail": "hit", "palette": {"bg": "#000"}}
        self.code = code
        self.calls = []

    async def chat(self, messages, model="auto", **kwargs):
        self.calls.append(messages)
        system = messages[0]["content"]
        text = json.dumps(self.spec) if "game designer" in system else self.code
        return {"text": text, "provider": "fake", "model": "fake-1", "usage": {}, "latency_ms": 1}


def test_extract_html_from_fences():
    wrapped = f"Sure! Here you go:\n```html\n{GAME}\n```\nEnjoy."
    assert extract_html(wrapped).startswith("<!doctype")


def test_extract_json_tolerates_preamble():
    assert extract_json('Here: {"title": "X"} done')["title"] == "X"


def test_validate_rejects_external_assets():
    bad = GAME.replace("<script>", '<script src="https://cdn.example/x.js"></script><script>')
    with pytest.raises(Exception):
        validate_game(bad)


def test_validate_flags_missing_loop():
    report = validate_game(GAME.replace("requestAnimationFrame", "noop"))
    assert report["loop"] is False


@pytest.mark.asyncio
async def test_generate_writes_playable_file(tmp_path, monkeypatch):
    monkeypatch.setattr(game_forge, "GAMES_DIR", tmp_path)
    forge = game_forge.GameForge(FakeGateway())
    out = await forge.generate("a ship dodging asteroids")
    path = tmp_path / out["filename"]
    assert path.is_file() and path.read_text().startswith("<!doctype")
    assert out["url"] == f"/games/{out['filename']}"
    assert out["title"] == "Orbit Runner"
    assert all(out["checks"].values())


@pytest.mark.asyncio
async def test_games_endpoint(client, tmp_path, monkeypatch):
    monkeypatch.setattr(game_forge, "GAMES_DIR", tmp_path)
    from app.main import app
    app.state.games = game_forge.GameForge(FakeGateway())
    res = await client.post("/api/v1/games/generate", json={"prompt": "a ship dodging asteroids"})
    assert res.status_code == 200, res.text
    body = res.json()
    assert body["checks"]["canvas"] and body["bytes"] > 500
