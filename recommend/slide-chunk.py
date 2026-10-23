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

def find_best_chunks_per_slide(
    tx, topic_name, document_name, section_name, chapter_name,
    book_name, module_name, page_paths
):
    """
    为 Chapter 下的每个 Slide，在所有已匹配 Page 的 Chunk 中找共享 Entity 最多的前 5 个。
    """
    query = """
    MATCH (t:Topic {title: $topic_name})-[:CONTAINS]->(d:Document {name: $document_name})
          -[:HAS_SECTION]->(s:Section {name: $section_name})
          -[:HAS_CHAPTER]->(c:Chapter {name: $chapter_name})-[:HAS_SLIDE]->(slide:Slide)
    WHERE slide.name IS NOT NULL

    CALL {
        WITH slide
        MATCH (slide)-[:MENTIONS]->(slide_e:Entity)
        RETURN count(DISTINCT slide_e) AS total_slide_entities
    }
    WITH t, d, s, c, slide, total_slide_entities
    WHERE total_slide_entities > 0

    MATCH (b:Book {name: $book_name})-[:HAS_MODULE]->(m:Module {name: $module_name})

    CALL {
        WITH slide, m, total_slide_entities
        UNWIND $page_paths AS page_path
        MATCH (m)-[:HAS_UNIT]->(u:Unit {name: page_path.unit_name})-[:HAS_PAGE]->(p:Page)
        WHERE p.name = page_path.page_name AND p.page_idx = page_path.page_idx
        MATCH (p)-[:HAS_CHUNK]->(chunk:Chunk)-[:HAS_ENTITY]->(e:Entity)
        MATCH (slide)-[:MENTIONS]->(e:Entity)

        WITH chunk, p, u, count(DISTINCT e) AS shared_entity_count, total_slide_entities
        MATCH (chunk)-[:HAS_ENTITY]->(chunk_e:Entity)
        WITH chunk, p, u, shared_entity_count, total_slide_entities, count(DISTINCT chunk_e) AS total_chunk_entities
        WHERE total_chunk_entities > 0

        RETURN
            u.name AS unit_name,
            p.name AS page_name,
            p.page_idx AS page_idx,
            chunk.name AS chunk_id,
            shared_entity_count,
            total_chunk_entities,
            round(toFloat(shared_entity_count) / total_slide_entities * 100, 2) AS slide_ratio_percent,
            round(toFloat(shared_entity_count) / total_chunk_entities * 100, 2) AS chunk_ratio_percent
        ORDER BY shared_entity_count DESC, slide_ratio_percent DESC, chunk_ratio_percent DESC
        LIMIT 5
    }
    RETURN
        t.title AS topic_name,
        d.name AS document_name,
        s.name AS section_name,
        c.name AS chapter_name,
        slide.name AS slide_name,
        b.name AS book_name,
        m.name AS module_name,
        unit_name,
        page_name,
        page_idx,
        chunk_id,
        shared_entity_count,
        total_chunk_entities,
        slide_ratio_percent,
        chunk_ratio_percent
    """
    result = tx.run(
        query,
        topic_name=topic_name,
        document_name=document_name,
        section_name=section_name,
        chapter_name=chapter_name,
        book_name=book_name,
        module_name=module_name,
        page_paths=page_paths
    )
    return [
        {
            "topic_name": r["topic_name"],
            "document_name": r["document_name"],
            "section_name": r["section_name"],
            "chapter_name": r["chapter_name"],
            "slide_name": r["slide_name"],
            "book_name": r["book_name"],
            "module_name": r["module_name"],
            "unit_name": r["unit_name"],
            "page_name": r["page_name"],
            "page_idx": r["page_idx"],
            "chunk_id": r["chunk_id"],
            "shared_entity_count": r["shared_entity_count"],
            "total_chunk_entities": r["total_chunk_entities"],
            "slide_ratio(%)": r["slide_ratio_percent"],
            "chunk_ratio(%)": r["chunk_ratio_percent"]
        }
        for r in result
    ]

def process_chapter_page_csv(
    input_csv=os.path.join(RECOMMEND_DIR, "chapter_page.csv"),
    output_csv=os.path.join(RECOMMEND_DIR, "slide-chunk.csv")
):
    if not os.path.exists(input_csv):
        print(f"找不到输入的 CSV 文件: {input_csv}，请先生成 Chapter-Page 推荐结果！")
        return

    recommendations = {}
    with open(input_csv, mode="r", encoding="utf-8-sig") as f:
        reader = csv.DictReader(f)
        for row in reader:
            page_idx = row.get("page_idx")
            page_path = (
                row.get("unit_name"),
                row.get("page_name"),
                int(page_idx) if page_idx not in (None, "") else None
            )
            source = (
                row.get("topic_name"),
                row.get("document_name"),
                row.get("section_name"),
                row.get("chapter_name"),
                row.get("book_name"),
                row.get("module_name")
            )
            if all(source) and all(page_path[:2]) and page_path[2] is not None:
                recommendations.setdefault(source, set()).add(page_path)

    print(f"共读取到 {len(recommendations)} 组 Chapter 匹配，开始为各 Slide 跨推荐 Page 匹配 Chunk...")
    all_results = []
    db_name = NEO4J_DATABASE or "neo4j"
    with driver.session(database=db_name) as session:
        for i, (source, page_paths) in enumerate(recommendations.items()):
            topic_name, document_name, section_name, chapter_name, book_name, module_name = source
            page_path_rows = [
                {"unit_name": unit_name, "page_name": page_name, "page_idx": page_idx}
                for unit_name, page_name, page_idx in sorted(page_paths)
            ]
            print(f"[{i + 1}/{len(recommendations)}] 正在处理 Chapter【{chapter_name}】...")
            all_results.extend(
                session.execute_read(
                    find_best_chunks_per_slide,
                    topic_name,
                    document_name,
                    section_name,
                    chapter_name,
                    book_name,
                    module_name,
                    page_path_rows
                )
            )

    if not all_results:
        print("未匹配到任何 Slide 与已匹配 Page 下 Chunk 的共享 Entity。")

    fieldnames = [
        "topic_name",
        "document_name",
        "section_name",
        "chapter_name",
        "slide_name",
        "book_name",
        "module_name",
        "unit_name",
        "page_name",
        "page_idx",
        "chunk_id",
        "shared_entity_count",
        "total_chunk_entities",
        "slide_ratio(%)",
        "chunk_ratio(%)"
    ]
    with open(output_csv, mode="w", encoding="utf-8-sig", newline="") as f:
        writer = csv.DictWriter(f, fieldnames=fieldnames)
        writer.writeheader()
        writer.writerows(all_results)

    print(f"结果已成功写入 {output_csv}（共 {len(all_results)} 条 Slide-Chunk 匹配）！")

if __name__ == "__main__":
    try:
        process_chapter_page_csv()
    finally:
        driver.close()
