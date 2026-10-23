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

def find_best_units_per_section(tx, topic_name, document_name, book_name, module_name):
    """
    为 Document 下的每个 Section，在其已匹配 Module 的所有 Unit 中找共享 Entity 最多的前 5 个。
    """
    query = """
    MATCH (t:Topic {title: $topic_name})-[:CONTAINS]->(d:Document {name: $document_name})-[:HAS_SECTION]->(s:Section)
    WHERE s.name IS NOT NULL

    CALL {
        WITH s
        MATCH (s)-[:HAS_CHAPTER]->(:Chapter)-[:HAS_SLIDE]->(:Slide)-[:MENTIONS]->(section_e:Entity)
        RETURN count(DISTINCT section_e) AS total_section_entities
    }
    WITH t, d, s, total_section_entities
    WHERE total_section_entities > 0

    MATCH (b:Book {name: $book_name})-[:HAS_MODULE]->(m:Module {name: $module_name})

    CALL {
        WITH s, m, total_section_entities
        MATCH (s)-[:HAS_CHAPTER]->(:Chapter)-[:HAS_SLIDE]->(:Slide)-[:MENTIONS]->(e:Entity)
        MATCH (m)-[:HAS_UNIT]->(u:Unit)-[:HAS_PAGE]->(:Page)-[:HAS_CHUNK]->(:Chunk)-[:HAS_ENTITY]->(e)

        WITH u, count(DISTINCT e) AS shared_entity_count, total_section_entities
        MATCH (u)-[:HAS_PAGE]->(:Page)-[:HAS_CHUNK]->(:Chunk)-[:HAS_ENTITY]->(unit_e:Entity)
        WITH u, shared_entity_count, total_section_entities, count(DISTINCT unit_e) AS total_unit_entities
        WHERE total_unit_entities > 0

        RETURN
            u.name AS unit_name,
            shared_entity_count,
            total_unit_entities,
            round(toFloat(shared_entity_count) / total_section_entities * 100, 2) AS section_ratio_percent,
            round(toFloat(shared_entity_count) / total_unit_entities * 100, 2) AS unit_ratio_percent
        ORDER BY shared_entity_count DESC, section_ratio_percent DESC, unit_ratio_percent DESC
        LIMIT 5
    }
    RETURN
        t.title AS topic_name,
        d.name AS document_name,
        s.name AS section_name,
        b.name AS book_name,
        m.name AS module_name,
        unit_name,
        shared_entity_count,
        total_unit_entities,
        section_ratio_percent,
        unit_ratio_percent
    """
    result = tx.run(
        query,
        topic_name=topic_name,
        document_name=document_name,
        book_name=book_name,
        module_name=module_name
    )
    return [
        {
            "topic_name": r["topic_name"],
            "document_name": r["document_name"],
            "section_name": r["section_name"],
            "book_name": r["book_name"],
            "module_name": r["module_name"],
            "unit_name": r["unit_name"],
            "shared_entity_count": r["shared_entity_count"],
            "total_unit_entities": r["total_unit_entities"],
            "section_ratio(%)": r["section_ratio_percent"],
            "unit_ratio(%)": r["unit_ratio_percent"]
        }
        for r in result
    ]

def process_doc_module_csv(
    input_csv=os.path.join(RECOMMEND_DIR, "doc_module.csv"),
    output_csv=os.path.join(RECOMMEND_DIR, "section_unit.csv")
):
    if not os.path.exists(input_csv):
        print(f"找不到输入的 CSV 文件: {input_csv}，请先生成 Document-Module 推荐结果！")
        return

    recommendations = set()
    with open(input_csv, mode="r", encoding="utf-8-sig") as f:
        reader = csv.DictReader(f)
        for row in reader:
            values = (
                row.get("topic_name"),
                row.get("document_name"),
                row.get("book_name"),
                row.get("module_name")
            )
            if all(values):
                recommendations.add(values)

    print(f"共读取到 {len(recommendations)} 组 Document-Module 匹配，开始为各 Section 匹配 Unit...")
    all_results = []
    db_name = NEO4J_DATABASE or "neo4j"
    with driver.session(database=db_name) as session:
        for i, (topic_name, document_name, book_name, module_name) in enumerate(recommendations):
            print(f"[{i + 1}/{len(recommendations)}] 正在处理 Document【{document_name}】...")
            all_results.extend(
                session.execute_read(
                    find_best_units_per_section,
                    topic_name,
                    document_name,
                    book_name,
                    module_name
                )
            )

    if not all_results:
        print("未匹配到任何 Section 与已匹配 Module 下 Unit 的共享 Entity。")

    fieldnames = [
        "topic_name",
        "document_name",
        "section_name",
        "book_name",
        "module_name",
        "unit_name",
        "shared_entity_count",
        "total_unit_entities",
        "section_ratio(%)",
        "unit_ratio(%)"
    ]
    with open(output_csv, mode="w", encoding="utf-8-sig", newline="") as f:
        writer = csv.DictWriter(f, fieldnames=fieldnames)
        writer.writeheader()
        writer.writerows(all_results)

    print(f"结果已成功写入 {output_csv}（共 {len(all_results)} 条 Section-Unit 匹配）！")

if __name__ == "__main__":
    try:
        process_doc_module_csv()
    finally:
        driver.close()
