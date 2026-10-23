import os
import csv
from neo4j import GraphDatabase
from dotenv import load_dotenv

# 加载环境变量
load_dotenv()
NEO4J_URI = os.getenv("NEO4J_URI", "neo4j://127.0.0.1:7687")
NEO4J_USERNAME = os.getenv("NEO4J_USERNAME", "neo4j")
NEO4J_PASSWORD = os.getenv("NEO4J_PASSWORD", "neo4j")
NEO4J_DATABASE = os.getenv("NEO4J_DATABASE", "neo4j")

driver = GraphDatabase.driver(NEO4J_URI, auth=(NEO4J_USERNAME, NEO4J_PASSWORD))
RECOMMEND_DIR = os.path.dirname(os.path.abspath(__file__))

def find_shared_entity_books_with_ratios(tx):
    """
    通过图路径匹配，查找每个 Topic 共享 Entity 最多的前 5 个 Book，并计算占比
    """
    query = """
    MATCH (t:Topic)
    WHERE t.title IS NOT NULL
    
    // 1. 获取该 Topic 关联的所有 Entity 总数（去重），并用 WITH 传递过滤
    CALL {
        WITH t
        MATCH (t)-[:CONTAINS]->(:Document)-[:HAS_SECTION]->(:Section)-[:HAS_CHAPTER]->(:Chapter)-[:HAS_SLIDE]->(:Slide)-[:MENTIONS]->(topic_e:Entity)
        RETURN count(DISTINCT topic_e) AS total_topic_entities
    }
    WITH t, total_topic_entities
    WHERE total_topic_entities > 0
    
    // 2. 匹配 Book 并找出共同的 Entity，计算占比并按共享数量降序取前 3
    CALL {
        WITH t, total_topic_entities
        MATCH (t)-[:CONTAINS]->(:Document)-[:HAS_SECTION]->(:Section)-[:HAS_CHAPTER]->(:Chapter)-[:HAS_SLIDE]->(:Slide)-[:MENTIONS]->(e:Entity)
        MATCH (b:Book)-[:HAS_MODULE]->(:Module)-[:HAS_UNIT]->(:Unit)-[:HAS_PAGE]->(:Page)-[:HAS_CHUNK]->(:Chunk)-[:HAS_ENTITY]->(e)
        WHERE b.name IS NOT NULL
        
        WITH b, count(DISTINCT e) AS shared_entity_count, total_topic_entities
        
        // 获取该 Book 关联的所有 Entity 总数（去重）
        MATCH (b)-[:HAS_MODULE]->(:Module)-[:HAS_UNIT]->(:Unit)-[:HAS_PAGE]->(:Page)-[:HAS_CHUNK]->(:Chunk)-[:HAS_ENTITY]->(book_e:Entity)
        WITH b, shared_entity_count, total_topic_entities, count(DISTINCT book_e) AS total_book_entities
        WHERE total_book_entities > 0
        
        RETURN 
            b.name AS book_name, 
            shared_entity_count,
            total_book_entities,
            round(toFloat(shared_entity_count) / total_book_entities * 100, 2) AS book_ratio_percent,
            round(toFloat(shared_entity_count) / total_topic_entities * 100, 2) AS topic_ratio_percent
        ORDER BY shared_entity_count DESC
        LIMIT 5
    }
    RETURN t.title AS topic_name, book_name, shared_entity_count, total_book_entities, book_ratio_percent, topic_ratio_percent
    """
    result = tx.run(query)
    return [
        {
            "topic_name": r["topic_name"],
            "book_name": r["book_name"],
            "shared_entity_count": r["shared_entity_count"],
            "total_book_entities": r["total_book_entities"],
            "book_ratio(%)": r["book_ratio_percent"],
            "topic_ratio(%)": r["topic_ratio_percent"]
        } 
        for r in result
    ]

def export_topic_book_with_ratios(output_filename=os.path.join(RECOMMEND_DIR, "topic_book.csv")):
    print("正在连接数据库，计算共享 Entity 及其占比...")
    db_name = NEO4J_DATABASE or "neo4j"
    
    with driver.session(database=db_name) as session:
        results = session.execute_read(find_shared_entity_books_with_ratios)
        
    if not results:
        print("未查询到符合条件的结果，请检查路径和节点名称是否存在！")
        return
        
    print(f"查询完成！共获取到 {len(results)} 条关联记录。正在写入 CSV 文件: {output_filename}")
    
    # 写入 CSV 文件
    fieldnames = [
        "topic_name", 
        "book_name", 
        "shared_entity_count", 
        "total_book_entities", 
        "book_ratio(%)", 
        "topic_ratio(%)"
    ]
    
    with open(output_filename, mode="w", encoding="utf-8-sig", newline="") as f:
        writer = csv.DictWriter(f, fieldnames=fieldnames)
        writer.writeheader()
        writer.writerows(results)
        
    print(f"结果已成功写入 {output_filename}！")

if __name__ == "__main__":
    try:
        export_topic_book_with_ratios()
    finally:
        driver.close()