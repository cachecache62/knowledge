import os

model_path = r"D:\ai_models\modelscope_cache\models\BAAI\bge-m3"

print(model_path)
print(os.path.exists(model_path))
print(os.path.isdir(model_path))