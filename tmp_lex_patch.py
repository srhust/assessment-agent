"""R4a 阶段1：lexicon_r3c 词条扩展（一次性补丁脚本，运行后可删除）。"""
import json
from pathlib import Path

P = Path("src/assessment_agent/tools/lexicon_r3c.json")
data = json.loads(P.read_text(encoding="utf-8"))
zh, en = data["zh"], data["en"]

zh.update({
    "毛笔": {"def": "以竹管兽毫制成的书写绘画工具", "tools": ["墨水"], "hyper": "文具"},
    "墨水": {"def": "书写绘画用的有色液体"},
    "笔筒": {"def": "案头插放笔类文具的容器", "parts": ["毛笔", "笔"], "hyper": "文具"},
    "画架": {"def": "绘画时张挂画布、陈列画作的支架"},
    "挥霍": {"def": "毫无节制地浪费钱财", "syn": "浪费", "ant": ["吝啬", "节俭"]},
    "勤勉": {"def": "努力不懈怠", "syn": ["勤奋", "刻苦"], "ant": ["懒惰", "懈怠"]},
})
zh["画家"]["tools"] = ["颜料", "画笔", "画架"]
zh["吝啬"]["ant"] = ["慷慨", "大方", "挥霍"]

en.update({
    "handle": {"def": "the part of a tool made to be held by hand", "part_of": ["knife"]},
    "blade": {"def": "the sharp cutting part of a knife", "part_of": ["knife"]},
    "metal": {"def": "a hard shiny material such as iron or copper"},
    "paper": {"def": "thin sheets used for writing or printing"},
    "leaf": {"def": "a single page of a book or magazine", "syn": ["page"]},
    "forest": {"def": "a large area covered with trees"},
    "hunter": {"def": "a person who hunts wild animals", "place": "forest"},
})
en["knife"]["parts"] = ["handle", "blade"]
en["knife"]["made_of"] = "metal"
en["page"]["made_of"] = "paper"
en["page"]["function"] = ["reading"]

P.write_text(json.dumps(data, ensure_ascii=False, indent=1) + "\n", encoding="utf-8")
print("lexicon patched:", len(zh), "zh /", len(en), "en")
