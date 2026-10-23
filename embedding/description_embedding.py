import os
import re
from zhipuai import ZhipuAI
from neo4j import GraphDatabase
from dotenv import load_dotenv

# 加载环境变量
load_dotenv()
ZHIPU_API_KEY = os.getenv("ZHIPU_API_KEY")
NEO4J_URI = os.getenv("NEO4J_URI", "neo4j://127.0.0.1:7687")
NEO4J_USERNAME = os.getenv("NEO4J_USERNAME", "neo4j")
NEO4J_PASSWORD = os.getenv("NEO4J_PASSWORD", "neo4j")
NEO4J_DATABASE = os.getenv("NEO4J_DATABASE", "neo4j")

client = ZhipuAI(api_key=ZHIPU_API_KEY)
driver = GraphDatabase.driver(NEO4J_URI, auth=(NEO4J_USERNAME, NEO4J_PASSWORD))

def get_unembedded_entities(tx):
    """读事务：获取所有缺少向量的实体"""
    query = """
    MATCH (e:Entity) 
    WHERE e.embedding IS NULL 
    RETURN elementId(e) AS id, e.name AS name, e.description AS desc
    """
    result = tx.run(query)
    return [{"id": r["id"], "name": r["name"], "desc": r["desc"] or ""} for r in result]

def batch_update_embeddings(tx, data_list):
    """写事务：利用 UNWIND 一次性批量写回 Neo4j，极大减少网络 IO"""
    query = """
    UNWIND $batch AS item
    MATCH (e:Entity) WHERE elementId(e) = item.id
    SET e.embedding = item.vec
    """
    tx.run(query, batch=data_list)

def update_embeddings():
    print("正在连接数据库，查找需要向量化的实体...")
    db_name = NEO4J_DATABASE or "neo4j"
    
    # 1. 专门的读会话
    with driver.session(database=db_name) as session:
        nodes = session.execute_read(get_unembedded_entities)
        
    if not nodes:
        print("所有实体都已经拥有向量，无需更新！")
        return
        
    print(f"发现 {len(nodes)} 个缺少向量的实体。开始调用智谱 API 生成...")
    
    pending_updates = []
    
    for i, node in enumerate(nodes):
        text_to_embed = f"实体名称: {node['name']}\n描述: {node['desc']}"
        try:
            response = client.embeddings.create(model="embedding-2", input=[text_to_embed])
            vec = response.data[0].embedding
            
            pending_updates.append({"id": node["id"], "vec": vec})
            
            # 每 20 条或者到最后一条时，执行一次批量写回
            if (i + 1) % 20 == 0 or (i + 1) == len(nodes):
                with driver.session(database=db_name) as write_session:
                    write_session.execute_write(batch_update_embeddings, pending_updates)
                print(f"已完成向量化并写入：{i + 1} / {len(nodes)}...")
                pending_updates.clear() # 清空当前批次缓冲
                
        except Exception as e:
            print(f"实体 {node['name']} 向量化过程中发生错误: {e}")
                
    print("向量化全部完成！")

if __name__ == "__main__":
    try:
        update_embeddings()
    finally:
        driver.close()