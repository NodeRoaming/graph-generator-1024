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

def find_best_pages_per_chapter(
    tx, topic_name, document_name, section_name, book_name, module_name, unit_names
):
    """
    为 Section 下的每个 Chapter，在其所有已匹配 Unit 的 Page 中找共享 Entity 最多的前 5 个。
    """
    query = """
    MATCH (t:Topic {title: $topic_name})-[:CONTAINS]->(d:Document {name: $document_name})
          -[:HAS_SECTION]->(s:Section {name: $section_name})-[:HAS_CHAPTER]->(c:Chapter)
    WHERE c.name IS NOT NULL

    CALL {
        WITH c
        MATCH (c)-[:HAS_SLIDE]->(:Slide)-[:MENTIONS]->(chapter_e:Entity)
        RETURN count(DISTINCT chapter_e) AS total_chapter_entities
    }
    WITH t, d, s, c, total_chapter_entities
    WHERE total_chapter_entities > 0

    MATCH (b:Book {name: $book_name})-[:HAS_MODULE]->(m:Module {name: $module_name})

    CALL {
        WITH c, m, total_chapter_entities
        MATCH (m)-[:HAS_UNIT]->(u:Unit)
        WHERE u.name IN $unit_names
        MATCH (u)-[:HAS_PAGE]->(p:Page)-[:HAS_CHUNK]->(:Chunk)-[:HAS_ENTITY]->(e:Entity)
        MATCH (c)-[:HAS_SLIDE]->(:Slide)-[:MENTIONS]->(e:Entity)

        WITH p, u, count(DISTINCT e) AS shared_entity_count, total_chapter_entities
        MATCH (p)-[:HAS_CHUNK]->(:Chunk)-[:HAS_ENTITY]->(page_e:Entity)
        WITH p, u, shared_entity_count, total_chapter_entities, count(DISTINCT page_e) AS total_page_entities
        WHERE total_page_entities > 0

        RETURN
            u.name AS unit_name,
            p.name AS page_name,
            p.page_idx AS page_idx,
            shared_entity_count,
            total_page_entities,
            round(toFloat(shared_entity_count) / total_chapter_entities * 100, 2) AS chapter_ratio_percent,
            round(toFloat(shared_entity_count) / total_page_entities * 100, 2) AS page_ratio_percent
        ORDER BY shared_entity_count DESC, chapter_ratio_percent DESC, page_ratio_percent DESC
        LIMIT 5
    }
    RETURN
        t.title AS topic_name,
        d.name AS document_name,
        s.name AS section_name,
        c.name AS chapter_name,
        b.name AS book_name,
        m.name AS module_name,
        page_name,
        unit_name,
        page_idx,
        shared_entity_count,
        total_page_entities,
        chapter_ratio_percent,
        page_ratio_percent
    """
    result = tx.run(
        query,
        topic_name=topic_name,
        document_name=document_name,
        section_name=section_name,
        book_name=book_name,
        module_name=module_name,
        unit_names=unit_names
    )
    return [
        {
            "topic_name": r["topic_name"],
            "document_name": r["document_name"],
            "section_name": r["section_name"],
            "chapter_name": r["chapter_name"],
            "book_name": r["book_name"],
            "module_name": r["module_name"],
            "unit_name": r["unit_name"],
            "page_name": r["page_name"],
            "page_idx": r["page_idx"],
            "shared_entity_count": r["shared_entity_count"],
            "total_page_entities": r["total_page_entities"],
            "chapter_ratio(%)": r["chapter_ratio_percent"],
            "page_ratio(%)": r["page_ratio_percent"]
        }
        for r in result
    ]

def process_section_unit_csv(
    input_csv=os.path.join(RECOMMEND_DIR, "section_unit.csv"),
    output_csv=os.path.join(RECOMMEND_DIR, "chapter_page.csv")
):
    if not os.path.exists(input_csv):
        print(f"找不到输入的 CSV 文件: {input_csv}，请先生成 Section-Unit 推荐结果！")
        return

    recommendations = {}
    with open(input_csv, mode="r", encoding="utf-8-sig") as f:
        reader = csv.DictReader(f)
        for row in reader:
            values = (
                row.get("topic_name"),
                row.get("document_name"),
                row.get("section_name"),
                row.get("book_name"),
                row.get("module_name")
            )
            if all(values):
                unit_name = row.get("unit_name")
                if unit_name:
                    recommendations.setdefault(values, set()).add(unit_name)

    print(f"共读取到 {len(recommendations)} 组 Section-Module 匹配，开始为各 Chapter 跨 Unit 匹配 Page...")
    all_results = []
    db_name = NEO4J_DATABASE or "neo4j"
    with driver.session(database=db_name) as session:
        for i, ((topic_name, document_name, section_name, book_name, module_name), unit_names) in enumerate(recommendations.items()):
            print(f"[{i + 1}/{len(recommendations)}] 正在处理 Section【{section_name}】...")
            all_results.extend(
                session.execute_read(
                    find_best_pages_per_chapter,
                    topic_name,
                    document_name,
                    section_name,
                    book_name,
                    module_name,
                    sorted(unit_names)
                )
            )

    if not all_results:
        print("未匹配到任何 Chapter 与已匹配 Unit 下 Page 的共享 Entity。")

    fieldnames = [
        "topic_name",
        "document_name",
        "section_name",
        "chapter_name",
        "book_name",
        "module_name",
        "unit_name",
        "page_name",
        "page_idx",
        "shared_entity_count",
        "total_page_entities",
        "chapter_ratio(%)",
        "page_ratio(%)"
    ]
    with open(output_csv, mode="w", encoding="utf-8-sig", newline="") as f:
        writer = csv.DictWriter(f, fieldnames=fieldnames)
        writer.writeheader()
        writer.writerows(all_results)

    print(f"结果已成功写入 {output_csv}（共 {len(all_results)} 条 Chapter-Page 匹配）！")

if __name__ == "__main__":
    try:
        process_section_unit_csv()
    finally:
        driver.close()
