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

def find_best_module_per_document(tx, topic_name, book_names):
    """
    为 Topic 下的每个 Document，在其推荐 Book 所属的所有 Module 中找共享 Entity 最多的一个。
    """
    query = """
    MATCH (t:Topic {title: $topic_name})-[:CONTAINS]->(d:Document)
    WHERE d.name IS NOT NULL

    CALL {
        WITH d
        MATCH (d)-[:HAS_SECTION]->(:Section)-[:HAS_CHAPTER]->(:Chapter)-[:HAS_SLIDE]->(:Slide)-[:MENTIONS]->(doc_e:Entity)
        RETURN count(DISTINCT doc_e) AS total_document_entities
    }
    WITH t, d, total_document_entities
    WHERE total_document_entities > 0

    CALL {
        WITH d, total_document_entities
        MATCH (d)-[:HAS_SECTION]->(:Section)-[:HAS_CHAPTER]->(:Chapter)-[:HAS_SLIDE]->(:Slide)-[:MENTIONS]->(e:Entity)
        MATCH (b:Book)-[:HAS_MODULE]->(m:Module)-[:HAS_UNIT]->(:Unit)-[:HAS_PAGE]->(:Page)-[:HAS_CHUNK]->(:Chunk)-[:HAS_ENTITY]->(e)
        WHERE b.name IN $book_names AND m.name IS NOT NULL

        WITH b, m, total_document_entities, count(DISTINCT e) AS shared_entity_count
        MATCH (m)-[:HAS_UNIT]->(:Unit)-[:HAS_PAGE]->(:Page)-[:HAS_CHUNK]->(:Chunk)-[:HAS_ENTITY]->(module_e:Entity)
        WITH b, m, total_document_entities, shared_entity_count, count(DISTINCT module_e) AS total_module_entities
        WHERE total_module_entities > 0

        RETURN
            b.name AS book_name,
            m.name AS module_name,
            shared_entity_count,
            total_module_entities,
            round(toFloat(shared_entity_count) / total_document_entities * 100, 2) AS document_ratio_percent,
            round(toFloat(shared_entity_count) / total_module_entities * 100, 2) AS module_ratio_percent
        ORDER BY shared_entity_count DESC, document_ratio_percent DESC, module_ratio_percent DESC
        LIMIT 1
    }
    RETURN
        t.title AS topic_name,
        d.name AS document_name,
        book_name,
        module_name,
        shared_entity_count,
        total_module_entities,
        document_ratio_percent,
        module_ratio_percent
    """
    result = tx.run(query, topic_name=topic_name, book_names=book_names)
    return [
        {
            "topic_name": r["topic_name"],
            "document_name": r["document_name"],
            "book_name": r["book_name"],
            "module_name": r["module_name"],
            "shared_entity_count": r["shared_entity_count"],
            "total_module_entities": r["total_module_entities"],
            "document_ratio(%)": r["document_ratio_percent"],
            "module_ratio(%)": r["module_ratio_percent"]
        }
        for r in result
    ]

def process_topic_book_csv(
    input_csv=os.path.join(RECOMMEND_DIR, "topic_book.csv"),
    output_csv=os.path.join(RECOMMEND_DIR, "doc_module.csv")
):
    if not os.path.exists(input_csv):
        print(f"找不到输入的 CSV 文件: {input_csv}，请先生成 Topic-Book 推荐结果！")
        return

    print(f"正在读取输入文件: {input_csv} ...")
    topic_books = {}
    with open(input_csv, mode="r", encoding="utf-8-sig") as f:
        reader = csv.DictReader(f)
        for row in reader:
            topic_name = row.get("topic_name")
            book_name = row.get("book_name")
            if topic_name and book_name:
                topic_books.setdefault(topic_name, set()).add(book_name)

    print(f"共读取到 {len(topic_books)} 个 Topic，开始为各 Document 匹配 Module...")
    all_results = []
    db_name = NEO4J_DATABASE or "neo4j"

    with driver.session(database=db_name) as session:
        for i, (topic_name, book_names) in enumerate(topic_books.items()):
            print(f"[{i + 1}/{len(topic_books)}] 正在处理 Topic【{topic_name}】...")
            all_results.extend(
                session.execute_read(
                    find_best_module_per_document,
                    topic_name,
                    list(book_names)
                )
            )

    if not all_results:
        print("未匹配到任何 Document 与推荐 Book 下 Module 的共享 Entity。")

    fieldnames = [
        "topic_name",
        "document_name",
        "book_name",
        "module_name",
        "shared_entity_count",
        "total_module_entities",
        "document_ratio(%)",
        "module_ratio(%)"
    ]
    with open(output_csv, mode="w", encoding="utf-8-sig", newline="") as f:
        writer = csv.DictWriter(f, fieldnames=fieldnames)
        writer.writeheader()
        writer.writerows(all_results)

    print(f"结果已成功写入 {output_csv}（共 {len(all_results)} 条 Document-Module 匹配）！")

if __name__ == "__main__":
    try:
        process_topic_book_csv()
    finally:
        driver.close()
