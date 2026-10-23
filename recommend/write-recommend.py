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

def import_recommendations(tx, query, rows):
    result = tx.run(query, rows=rows)
    record = result.single()
    return record["written"] if record else 0

def replace_recommendations(tx, clear_query, clear_property_query, query, rows):
    tx.run(clear_query).consume()
    tx.run(clear_property_query).consume()
    return import_recommendations(tx, query, rows)

def read_csv_rows(filename):
    path = os.path.join(RECOMMEND_DIR, filename)
    if not os.path.exists(path):
        print(f"未找到文件 {path}，跳过对应推荐导入。")
        return None

    with open(path, mode="r", encoding="utf-8-sig", newline="") as f:
        return list(csv.DictReader(f))

def limit_recommendations(rows, source_fields, target_fields, ratio_field, limit):
    grouped = {}
    for row in rows:
        source = tuple(row.get(field) for field in source_fields)
        target = tuple(row.get(field) for field in target_fields)
        if not all(source) or not all(target):
            continue
        try:
            shared_count = int(row.get("shared_entity_count") or 0)
            ratio = float(row.get(ratio_field) or 0)
        except ValueError:
            continue

        current = grouped.setdefault(source, {})
        previous = current.get(target)
        rank = (shared_count, ratio)
        if previous is None or rank > previous[0]:
            current[target] = (rank, row)

    limited_rows = []
    for targets in grouped.values():
        ranked = sorted(
            targets.values(),
            key=lambda item: (item[0][0], item[0][1]),
            reverse=True
        )
        limited_rows.extend(row for _, row in ranked[:limit])
    return limited_rows

def process_and_write_to_neo4j():
    db_name = NEO4J_DATABASE or "neo4j"
    recommendations = [
        (
            "topic_book.csv",
            "RECOMMEND_BOOK",
            ("topic_name",),
            ("book_name",),
            "topic_ratio(%)",
            5,
            "MATCH ()-[r:RECOMMEND_BOOK]->() DELETE r",
            "MATCH (t:Topic) REMOVE t.recommend_book",
            """
            UNWIND $rows AS row
            MATCH (t:Topic {title: row.topic_name})
            MATCH (b:Book {name: row.book_name})
            MERGE (t)-[r:RECOMMEND_BOOK]->(b)
            SET r.similarity = toFloat(row["topic_ratio(%)"]),
                r.shared_entity_count = toInteger(row.shared_entity_count)
            WITH t, collect(DISTINCT b.name) AS paths, count(r) AS source_written
            SET t.recommend_book = paths
            RETURN sum(source_written) AS written
            """
        ),
        (
            "doc_module.csv",
            "RECOMMEND_MODULE",
            ("topic_name", "document_name"),
            ("book_name", "module_name"),
            "document_ratio(%)",
            1,
            "MATCH ()-[r:RECOMMEND_MODULE]->() DELETE r",
            "MATCH (d:Document) REMOVE d.recommend_module",
            """
            UNWIND $rows AS row
            MATCH (t:Topic {title: row.topic_name})-[:CONTAINS]->(d:Document {name: row.document_name})
            MATCH (b:Book {name: row.book_name})-[:HAS_MODULE]->(m:Module {name: row.module_name})
            MERGE (d)-[r:RECOMMEND_MODULE]->(m)
            SET r.similarity = toFloat(row["document_ratio(%)"]),
                r.shared_entity_count = toInteger(row.shared_entity_count)
            WITH d, collect(DISTINCT b.name + "." + m.name) AS paths, count(r) AS source_written
            SET d.recommend_module = paths
            RETURN sum(source_written) AS written
            """
        ),
        (
            "section_unit.csv",
            "RECOMMEND_UNIT",
            ("topic_name", "document_name", "section_name"),
            ("book_name", "module_name", "unit_name"),
            "section_ratio(%)",
            5,
            "MATCH ()-[r:RECOMMEND_UNIT]->() DELETE r",
            "MATCH (s:Section) REMOVE s.recommend_unit",
            """
            UNWIND $rows AS row
            MATCH (t:Topic {title: row.topic_name})-[:CONTAINS]->(d:Document {name: row.document_name})
                  -[:HAS_SECTION]->(s:Section {name: row.section_name})
            MATCH (b:Book {name: row.book_name})-[:HAS_MODULE]->(m:Module {name: row.module_name})
                  -[:HAS_UNIT]->(u:Unit {name: row.unit_name})
            MERGE (s)-[r:RECOMMEND_UNIT]->(u)
            SET r.similarity = toFloat(row["section_ratio(%)"]),
                r.shared_entity_count = toInteger(row.shared_entity_count)
            WITH s, collect(DISTINCT b.name + "." + m.name + "." + u.name) AS paths, count(r) AS source_written
            SET s.recommend_unit = paths
            RETURN sum(source_written) AS written
            """
        ),
        (
            "chapter_page.csv",
            "RECOMMEND_PAGE",
            ("topic_name", "document_name", "section_name", "chapter_name"),
            ("book_name", "module_name", "unit_name", "page_name", "page_idx"),
            "chapter_ratio(%)",
            5,
            "MATCH ()-[r:RECOMMEND_PAGE]->() DELETE r",
            "MATCH (c:Chapter) REMOVE c.recommend_page",
            """
            UNWIND $rows AS row
            MATCH (t:Topic {title: row.topic_name})-[:CONTAINS]->(d:Document {name: row.document_name})
                  -[:HAS_SECTION]->(s:Section {name: row.section_name})
                  -[:HAS_CHAPTER]->(c:Chapter {name: row.chapter_name})
            MATCH (b:Book {name: row.book_name})-[:HAS_MODULE]->(m:Module {name: row.module_name})
                  -[:HAS_UNIT]->(u:Unit {name: row.unit_name})-[:HAS_PAGE]->(p:Page)
            WHERE p.name = row.page_name AND p.page_idx = toInteger(row.page_idx)
            MERGE (c)-[r:RECOMMEND_PAGE]->(p)
            SET r.similarity = toFloat(row["chapter_ratio(%)"]),
                r.shared_entity_count = toInteger(row.shared_entity_count)
            WITH c, collect(DISTINCT b.name + "." + m.name + "." + u.name + "." + p.name) AS paths,
                 count(r) AS source_written
            SET c.recommend_page = paths
            RETURN sum(source_written) AS written
            """
        ),
        (
            "slide-chunk.csv",
            "RECOMMEND_CHUNK",
            ("topic_name", "document_name", "section_name", "chapter_name", "slide_name"),
            ("book_name", "module_name", "unit_name", "page_name", "page_idx", "chunk_id"),
            "slide_ratio(%)",
            5,
            "MATCH ()-[r:RECOMMEND_CHUNK]->() DELETE r",
            "MATCH (slide:Slide) REMOVE slide.recommend_chunk",
            """
            UNWIND $rows AS row
            MATCH (t:Topic {title: row.topic_name})-[:CONTAINS]->(d:Document {name: row.document_name})
                  -[:HAS_SECTION]->(s:Section {name: row.section_name})
                  -[:HAS_CHAPTER]->(c:Chapter {name: row.chapter_name})
                  -[:HAS_SLIDE]->(slide:Slide {name: row.slide_name})
            MATCH (b:Book {name: row.book_name})-[:HAS_MODULE]->(m:Module {name: row.module_name})
                  -[:HAS_UNIT]->(u:Unit {name: row.unit_name})-[:HAS_PAGE]->(p:Page)
                  -[:HAS_CHUNK]->(chunk:Chunk {name: row.chunk_id})
            WHERE p.name = row.page_name AND p.page_idx = toInteger(row.page_idx)
            MERGE (slide)-[r:RECOMMEND_CHUNK]->(chunk)
            SET r.similarity = toFloat(row["slide_ratio(%)"]),
                r.shared_entity_count = toInteger(row.shared_entity_count)
            WITH slide, collect(DISTINCT b.name + "." + m.name + "." + u.name + "." + p.name + "." + chunk.name) AS paths,
                 count(r) AS source_written
            SET slide.recommend_chunk = paths
            RETURN sum(source_written) AS written
            """
        )
    ]

    with driver.session(database=db_name) as session:
        for (
            filename, relationship_type, source_fields, target_fields,
            ratio_field, limit, clear_query, clear_property_query, query
        ) in recommendations:
            rows = read_csv_rows(filename)
            if rows is None:
                continue
            selected_rows = limit_recommendations(
                rows, source_fields, target_fields, ratio_field, limit
            )
            written = session.execute_write(
                replace_recommendations,
                clear_query,
                clear_property_query,
                query,
                selected_rows
            )
            print(f"{relationship_type}: 成功写入/更新 {written} 条推荐关系（来自 {filename}）。")

if __name__ == "__main__":
    try:
        process_and_write_to_neo4j()
        print("五级推荐关系已导入 Neo4j。")
    finally:
        driver.close()
