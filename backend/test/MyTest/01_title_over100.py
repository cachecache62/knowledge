import json

import os
from pathlib import Path

print(os.getcwd())

file_path = Path(__file__).parent / "data.json"

with open(file_path, "r", encoding="utf-8") as f:
    data = json.load(f)

count = 0
for item in data:
    title = item.get("title", "")
    # if len(title) > 100:
    print(len(title), title)
    count += len(title)


print("总长度:", count)
print("总条数:", len(data))