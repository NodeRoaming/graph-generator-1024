import os
import re
from dotenv import load_dotenv
from neo4j import GraphDatabase
from zhipuai import ZhipuAI

# 加载环境变量
load_dotenv()
ZHIPU_API_KEY = os.getenv("ZHIPU_API_KEY")
NEO4J_URI = os.getenv("NEO4J_URI", "neo4j://127.0.0.1:7687")
NEO4J_USERNAME = os.getenv("NEO4J_USERNAME", "neo4j")
NEO4J_PASSWORD = os.getenv("NEO4J_PASSWORD", "neo4j")
NEO4J_DATABASE = os.getenv("NEO4J_DATABASE", "neo4j")

client = ZhipuAI(api_key=ZHIPU_API_KEY)
driver = GraphDatabase.driver(NEO4J_URI, auth=(NEO4J_USERNAME, NEO4J_PASSWORD))


def get_unembedded_chunk_nodes(tx):
    """读事务：获取所有包含 chunk 且尚未计算 chunk_embedding 的节点"""
    query = """
    MATCH (n) 
    WHERE n.chunk IS NOT NULL 
      AND trim(n.chunk) <> '' 
      AND n.chunk_embedding IS NULL 
    RETURN elementId(n) AS id, coalesce(n.name, n.title, 'Unamed Node') AS name, n.chunk AS chunk
    """
    result = tx.run(query)
    return [
        {"id": r["id"], "name": r["name"], "chunk": r["chunk"]} for r in result
    ]


def batch_update_chunk_embeddings(tx, data_list):
    """写事务：批量将向量写入 chunk_embedding 属性"""
    query = """
    UNWIND $batch AS item
    MATCH (n) WHERE elementId(n) = item.id
    SET n.chunk_embedding = item.vec
    """
    tx.run(query, batch=data_list)


def update_chunk_embeddings():
    print("正在连接数据库，查找含有 chunk 且缺少 chunk_embedding 的节点...")
    db_name = NEO4J_DATABASE or "neo4j"

    # 1. 查询需要处理的节点
    with driver.session(database=db_name) as session:
        nodes = session.execute_read(get_unembedded_chunk_nodes)

    if not nodes:
        print("所有含 chunk 的节点都已经生成了 chunk_embedding，无需更新！")
        return

    print(
        f"发现 {len(nodes)} 个需要向量化的 chunk 节点。开始调用智谱 API 生成..."
    )

    pending_updates = []

    for i, node in enumerate(nodes):
        # 直接使用节点的 chunk 文本进行向量化
        text_to_embed = node["chunk"]
        try:
            response = client.embeddings.create(
                model="embedding-2", input=[text_to_embed]
            )
            vec = response.data[0].embedding

            pending_updates.append({"id": node["id"], "vec": vec})

            # 每 20 条或者到最后一条时，执行批量写回
            if (i + 1) % 20 == 0 or (i + 1) == len(nodes):
                with driver.session(database=db_name) as write_session:
                    write_session.execute_write(
                        batch_update_chunk_embeddings, pending_updates
                    )
                print(f"已完成向量化并写入：{i + 1} / {len(nodes)}...")
                pending_updates.clear()  # 清空当前批次缓冲

        except Exception as e:
            print(f"节点 【{node['name']}】 向量化过程中发生错误: {e}")

    print("🎉 所有 chunk 节点的向量化处理完成！")


if __name__ == "__main__":
    try:
        update_chunk_embeddings()
    finally:
        driver.close()