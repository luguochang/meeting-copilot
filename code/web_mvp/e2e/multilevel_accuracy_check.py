import os, sys, time, json
sys.path.insert(0, "code/web_mvp/backend"); sys.path.insert(0, "code/core")
for line in open(".env"):
    line=line.strip()
    if "=" in line and not line.startswith("#"):
        k,_,v=line.partition("="); os.environ[k.strip()]=v.strip().strip('"')
from meeting_copilot_web_mvp.llm_service import LlmConfig
from meeting_copilot_web_mvp.asr_correct import correct_transcript
from meeting_copilot_web_mvp.transcript_normalizer import normalize
from meeting_copilot_web_mvp import batch_transcribe
AUDIO = "data/asr_eval/local_samples/long_meeting.16k.wav"
EXPECTED_ENTITIES = ["支付服务","灰度","rollback","P99","staging","错误率","回滚","兼容性测试用例","v2.3","盯盘"]
config = LlmConfig.from_env()
# L1: ASR raw
t0=time.time(); raw = batch_transcribe.transcribe_file(__import__("pathlib").Path(AUDIO)); t1=time.time()
print(f"L1 ASR raw ({t1-t0:.1f}s): {raw[:120]}...")
# L3: normalizer only (current)
norm = normalize(raw)
# L2: LLM correction
t2=time.time(); corrected, usage, degraded = correct_transcript(raw, config); t3=time.time()
print(f"L2 LLM 修正 ({t3-t2:.1f}s, degraded={degraded}, tokens={usage['total_tokens']}): {corrected[:120]}...")
# L3 on corrected
final = normalize(corrected)
def recall(text):
    found = [e for e in EXPECTED_ENTITIES if e.lower() in text.lower()]
    return len(found)/len(EXPECTED_ENTITIES), found
r_raw,_ = recall(raw); r_norm,_ = recall(norm); r_corr,_ = recall(corrected); r_final,found = recall(final)
print(f"\n实体 recall (10 实体):")
print(f"  L1 raw:        {r_raw:.2f}")
print(f"  L1+L3 norm:    {r_norm:.2f}")
print(f"  L1+L2 LLM:     {r_corr:.2f}")
print(f"  L1+L2+L3 full: {r_final:.2f}  found={found}")
print(f"\n总时长: ASR {t1-t0:.1f}s + LLM {t3-t2:.1f}s = {t1-t0+t3-t2:.1f}s (音频 84s, RTF={ (t1-t0+t3-t2)/84:.2f})")
