import sys
sys.path.insert(0, 'code/asr_runtime')
from funasr import AutoModel
model = AutoModel(model="paraformer-zh", vad_model="fsmn-vad", punc_model="ct-punc", device="cpu", disable_update=True)
hotword = "checkout-service P99 staging error_rate 灰度 回滚 监控 兼容性 测试用例"
result = model.generate(input="code/asr_runtime/outputs/simulated-release-review.16k.wav", batch_size_s=60, merge_vad=True, merge_length_s=15, hotword=hotword)
text = "".join(item.get("text","") for item in result)
print("RAW:", text)
# normalize
import json
terms = json.load(open("configs/asr_terms.json"))["terms"]
norm = text
for k in sorted(terms, key=len, reverse=True):
    if k in norm: norm = norm.replace(k, terms[k])
print("NORMALIZED:", norm)
entities = ["checkout-service","error_rate","P99","staging","灰度","回滚","监控"]
found = [e for e in entities if e.lower() in norm.lower() or (e=="error_rate" and "错误率" in norm)]
print(f"recall {len(found)}/{len(entities)} = {len(found)/len(entities):.2f} found={found} missed={[e for e in entities if e not in found]}")
