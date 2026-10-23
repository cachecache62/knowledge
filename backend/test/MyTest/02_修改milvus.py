
# import os
 # 1. 获取milvus客户端对象
# from config import milvus_config
from utils.milvus_utils import get_milvus_client

        # 导入模块
from config.milvus_config import milvus_config  # 导入模块里的配置对象


from pymilvus import DataType
milvus_client = get_milvus_client()
if not milvus_client:
    print("milvus_client 连接失败，请检查配置或服务状态")



# milvus_client.alter_collection_field(
#     collection_name=milvus_config.chunks_collection,
#     field_name="title",
#     field_params={"max_length": 512}  # 将title字段的最大长度修改为512
# )


# # 修改字段
# milvus_client.alter_collection_field(
#     collection_name=milvus_config.chunks_collection,
#     field_name="parent_title",
#     field_params={"max_length": 512}
# )



# milvus_client.alter_collection_field(
#     collection_name=milvus_config.chunks_collection,
#     field_name="parent_title",
#     field_params={"max_length": 512}
# )

milvus_client.add_collection_field(
    collection_name=milvus_config.chunks_collection,
    field_name="create_time",
    field_params={"data_type": DataType.VARCHAR, "max_length": 19, "nullable": True}
)