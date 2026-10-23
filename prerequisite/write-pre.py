import os
import pandas as pd
from dotenv import load_dotenv
from neo4j import GraphDatabase

load_dotenv()

CSV_FILE_PATH = "structured_prerequisites_pruned_algo.csv"
SOURCE_COL = "source_doc_chapter"
TARGET_COL = "target_doc_chapter"

NEO4J_URI = os.getenv("NEO4J_URI", "bolt://localhost:7687")
NEO4J_USER = os.getenv("NEO4J_USER", "neo4j")
NEO4J_PASSWORD = os.getenv("NEO4J_PASSWORD")
NEO4J_DATABASE = os.getenv("NEO4J_DATABASE", "neo4j")

def import_prerequisites():
    if not os.path.exists(CSV_FILE_PATH):
        print(f"❌ 找不到文件: {CSV_FILE_PATH}")
        return

    df = pd.read_csv(CSV_FILE_PATH, encoding="utf-8-sig")

    records = []
    for _, row in df.iterrows():
        src = str(row[SOURCE_COL]).strip() if pd.notna(row[SOURCE_COL]) else ""
        tgt = str(row[TARGET_COL]).strip() if pd.notna(row[TARGET_COL]) else ""
        if src and tgt and src != tgt:
            records.append({"source": src, "target": tgt})

    print(f"📖 准备将 {len(records)} 条关系匹配并写入数据库 [{NEO4J_DATABASE}]...")

    driver = GraphDatabase.driver(NEO4J_URI, auth=(NEO4J_USER, NEO4J_PASSWORD))

    # 💡 核心 Cypher：完美适配 gen-pre_2.py 的完整层级路径与多属性兼容
    cypher_query = """
    UNWIND $batch AS row
    
    // 1. 匹配源节点 Source Chapter
    MATCH (l_src:Lesson)-[:HAS_TOPIC]->(:Topic)-[:CONTAINS]->(:Document)-[:HAS_SECTION]->(:Section)-[:HAS_CHAPTER]->(src:Chapter)
    WHERE src.full_id = row.source
       OR (l_src.title + '.' + coalesce(src.title, src.name, src.chapter, '')) = row.source
       OR (l_src.title + '."' + coalesce(src.title, src.name, src.chapter, '') + '"') = row.source
       
    // 2. 匹配目标节点 Target Chapter
    MATCH (l_tgt:Lesson)-[:HAS_TOPIC]->(:Topic)-[:CONTAINS]->(:Document)-[:HAS_SECTION]->(:Section)-[:HAS_CHAPTER]->(tgt:Chapter)
    WHERE tgt.full_id = row.target
       OR (l_tgt.title + '.' + coalesce(tgt.title, tgt.name, tgt.chapter, '')) = row.target
       OR (l_tgt.title + '."' + coalesce(tgt.title, tgt.name, tgt.chapter, '') + '"') = row.target

    // 3. 建立先修关系 (避免重复)
    MERGE (src)-[r:PREREQUISITE_FOR]->(tgt)
    """

    with driver.session(database=NEO4J_DATABASE) as session:
        print("🚀 正在匹配已有节点并创建 [:PREREQUISITE_FOR] 关系...")
        result = session.run(cypher_query, batch=records)
        summary = result.consume()

        print("\n" + "=" * 60)
        print(f"🎉 关系导入完成！变动统计：")
        print("=" * 60)
        print(f"  - 创建节点数 (Nodes Created): {summary.counters.nodes_created} (应为 0)")
        print(f"  - 创建关系数 (Relationships Created): {summary.counters.relationships_created}")

    driver.close()

if __name__ == "__main__":
    import_prerequisites()