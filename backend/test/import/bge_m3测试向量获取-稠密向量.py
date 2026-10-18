import numpy as np

from utils.embedding_utils import get_bge_m3_ef

docs = [
    "Artificial intelligence was founded as an academic discipline in 1956.",
    "Alan Turing was the first person to conduct substantial research in AI.",
    "Born in Maida Vale, London, Turing was raised in southern England.",
]
model = get_bge_m3_ef()
embeddings = model.encode_documents(docs)

#稠密向量的数据获取
# for emb in embeddings["dense"]:
#     print(type(emb.tolist()))
dense_list = [emb.tolist() for emb in embeddings["dense"]]

print(dense_list)

# print(dense_list)
print("dense_list 类型：", type(dense_list))
print("形状：", np.array(dense_list).shape)
print("文档数量：", len(dense_list))
print("每个向量维度：", len(dense_list[0]))
