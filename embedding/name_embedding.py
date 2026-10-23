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

def get_unembedded_nodes(tx):
    """读事务：获取所有含有 name 属性且缺少 name_embedding 的 Module/Unit/Chapter/Section 节点"""
    query = """
    MATCH (n)
    WHERE (n:Module OR n:Unit OR n:Chapter OR n:Section)
      AND n.name IS NOT NULL 
      AND n.name_embedding IS NULL 
    RETURN elementId(n) AS id, n.name AS name
    """
    result = tx.run(query)
    return [{"id": r["id"], "name": r["name"]} for r in result]

def batch_update_name_embeddings(tx, data_list):
    """写事务：利用 UNWIND 一次性批量写回 Neo4j，更新 name_embedding"""
    query = """
    UNWIND $batch AS item
    MATCH (n) WHERE elementId(n) = item.id
    SET n.name_embedding = item.vec
    """
    tx.run(query, batch=data_list)

def update_embeddings():
    print("正在连接数据库，查找需要生成 name_embedding 的节点...")
    db_name = NEO4J_DATABASE or "neo4j"
    
    # 1. 专门的读会话
    with driver.session(database=db_name) as session:
        nodes = session.execute_read(get_unembedded_nodes)
        
    if not nodes:
        print("所有符合条件的节点都已经拥有 name_embedding，无需更新！")
        return
        
    print(f"发现 {len(nodes)} 个缺少 name_embedding 的节点。开始调用智谱 API 生成...")
    
    pending_updates = []
    
    for i, node in enumerate(nodes):
        text_to_embed = node['name']
        try:
            response = client.embeddings.create(model="embedding-2", input=[text_to_embed])
            vec = response.data[0].embedding
            
            pending_updates.append({"id": node["id"], "vec": vec})
            
            # 每 20 条或者到最后一条时，执行一次批量写回
            if (i + 1) % 20 == 0 or (i + 1) == len(nodes):
                with driver.session(database=db_name) as write_session:
                    write_session.execute_write(batch_update_name_embeddings, pending_updates)
                print(f"已完成 name_embedding 写入：{i + 1} / {len(nodes)}...")
                pending_updates.clear() # 清空当前批次缓冲
                
        except Exception as e:
            print(f"节点 '{node['name']}' 向量化过程中发生错误: {e}")
                
    print("所有节点的 name_embedding 生成并写入完成！")

if __name__ == "__main__":
    try:
        update_embeddings()
    finally:
        driver.close()