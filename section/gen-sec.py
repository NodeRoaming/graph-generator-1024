import os
import re
from dotenv import load_dotenv
from neo4j import GraphDatabase

load_dotenv()

NEO4J_URI = os.getenv("NEO4J_URI", "bolt://localhost:7687")
NEO4J_USER = os.getenv("NEO4J_USER", "neo4j")
NEO4J_PASSWORD = os.getenv("NEO4J_PASSWORD")
NEO4J_DATABASE = os.getenv("NEO4J_DATABASE", "algorithm")


class DocumentSectionClustererGDS:
    def __init__(self, uri: str, auth: tuple, database: str = "algorithm"):
        self.driver = GraphDatabase.driver(uri, auth=auth)
        self.database = database

    def close(self):
        self.driver.close()

    def process_document(self, session, doc_element_id: str, doc_title: str):
        """
        1. 清理历史 Section 与旧 NEXT 关系
        2. 按 Slide 的 name 排序构建严格的【单链表 Slide NEXT 关系】
        3. 调用 Neo4j GDS Native Leiden 算法基于 Slide 划分社区，并映射到 Chapter
        4. 按 Chapter 的 name 顺序与社区断点生成 Section，安全重构 Document->Section->Chapter 层级
        """
        # ==========================================
        # Step 0: 兼容性清理历史 Section (防止多次运行残留)
        # ==========================================
        session.run("""
        MATCH (d:Document)-[:HAS_SECTION]->(sec:Section)-[:HAS_CHAPTER]->(c:Chapter)
        WHERE elementId(d) = $doc_id
        MERGE (d)-[:HAS_CHAPTER]->(c)
        WITH sec
        DETACH DELETE sec
        """, doc_id=doc_element_id)

        session.run("""
        MATCH (d:Document)-[:HAS_SECTION]->(sec:Section)
        WHERE elementId(d) = $doc_id AND NOT (sec)-[:HAS_CHAPTER]->(:Chapter)
        DETACH DELETE sec
        """, doc_id=doc_element_id)

        # 检查当前 Document 是否包含 Chapter
        chapters = session.run("""
        MATCH (d:Document)-[:HAS_CHAPTER]->(c:Chapter)
        WHERE elementId(d) = $doc_id
        RETURN elementId(c) AS cid
        """, doc_id=doc_element_id).data()

        if not chapters:
            print(f"[{self.database}] Document 《{doc_title}》 下未找到任何 Chapter，跳过处理。")
            return

        # ==========================================
        # Step 1: 建立严格的 Slide 单链表 NEXT 关系
        # ==========================================
        # 1.1 清理已有的 Slide NEXT 关系
        session.run("""
        MATCH (d:Document)-[:HAS_CHAPTER]->(:Chapter)-[:HAS_SLIDE]->(s:Slide)
        WHERE elementId(d) = $doc_id
        OPTIONAL MATCH (s)-[r:NEXT]->()
        DELETE r
        """, doc_id=doc_element_id)

        # 1.2 按 Slide 的 name 升序创建 (s1)-[:NEXT]->(s2) 单向链表 (若 name 相同则用 elementId 兜底)
        session.run("""
        MATCH (d:Document)-[:HAS_CHAPTER]->(:Chapter)-[:HAS_SLIDE]->(s:Slide)
        WHERE elementId(d) = $doc_id
        WITH s ORDER BY s.name ASC, elementId(s) ASC
        WITH collect(s) AS sorted_slides
        UNWIND range(0, size(sorted_slides) - 2) AS idx
        WITH sorted_slides[idx] AS curr_s, sorted_slides[idx + 1] AS next_s
        MERGE (curr_s)-[:NEXT]->(next_s)
        """, doc_id=doc_element_id)

        # ==========================================
        # Step 2: Neo4j GDS 内存图投影与 Leiden 社区划分 (基于 Slide)
        # ==========================================
        graph_name = f"graph_{re.sub(r'[^a-zA-Z0-9]', '', doc_element_id)}"

        # 销毁潜在的同名残留投影
        session.run(f"CALL gds.graph.drop('{graph_name}', false)")

        # 投影 Slide 单链表图结构
        session.run("""
        CALL gds.graph.project(
            $graph_name,
            'Slide',
            {
                NEXT: {
                    type: 'NEXT',
                    orientation: 'UNDIRECTED'
                }
            }
        )
        """, graph_name=graph_name)

        # 执行 Native Leiden 算法写回 slideCommunityId 到 Slide
        session.run("""
        CALL gds.leiden.write(
            $graph_name,
            {
                writeProperty: 'slideCommunityId',
                includeIntermediateCommunities: false,
                gamma: 1.0
            }
        )
        """, graph_name=graph_name)

        # 销毁内存投影图
        session.run(f"CALL gds.graph.drop('{graph_name}', false)")

        # 将 Slide 的社区属性映射并聚合到其所属的 Chapter 上（取最小的 slideCommunityId 作为 Chapter 的归属）
        session.run("""
        MATCH (d:Document)-[:HAS_CHAPTER]->(c:Chapter)-[:HAS_SLIDE]->(s:Slide)
        WHERE elementId(d) = $doc_id
        WITH c, min(s.slideCommunityId) AS commId
        SET c.communityId = commId
        """, doc_id=doc_element_id)

        # 清理临时建立的 Slide NEXT 关系
        session.run("""
        MATCH (d:Document)-[:HAS_CHAPTER]->(:Chapter)-[:HAS_SLIDE]->(s:Slide)
        WHERE elementId(d) = $doc_id
        OPTIONAL MATCH (s)-[r:NEXT]->()
        DELETE r
        """, doc_id=doc_element_id)

        # ==========================================
        # Step 3: 根据 Chapter 的 name 排序及社区断点切分组
        # ==========================================
        chapters_with_comm = session.run("""
        MATCH (d:Document)-[:HAS_CHAPTER]->(c:Chapter)
        WHERE elementId(d) = $doc_id
        RETURN elementId(c) AS chapter_id, c.communityId AS comm_id
        ORDER BY c.name ASC, elementId(c) ASC
        """, doc_id=doc_element_id).data()

        sections_group = []
        current_group = []
        last_comm = None

        for ch in chapters_with_comm:
            comm = ch['comm_id']
            if last_comm is None or comm == last_comm:
                current_group.append(ch['chapter_id'])
            else:
                if current_group:
                    sections_group.append(current_group)
                current_group = [ch['chapter_id']]
            last_comm = comm
        if current_group:
            sections_group.append(current_group)

        # ==========================================
        # Step 4: 重构图拓扑 (Document -> Section -> Chapter)
        # ==========================================
        safe_doc_name = re.sub(r'[^\w\s-]', '', str(doc_title)).strip().replace(' ', '_')
        valid_sec_count = 0

        for sec_idx, group_chapter_ids in enumerate(sections_group):
            if not group_chapter_ids:
                continue

            sec_id = f"{safe_doc_name}_sec_{sec_idx + 1}"
            sec_name = f"Section {sec_idx + 1}"

            result = session.run("""
            UNWIND $c_ids AS c_id
            MATCH (d:Document)-[r_doc:HAS_CHAPTER]->(c:Chapter)
            WHERE elementId(d) = $doc_id AND elementId(c) = c_id
            
            MERGE (sec:Section {id: $sec_id})
               ON CREATE SET sec.name = $sec_name
               ON MATCH SET sec.name = $sec_name
            MERGE (d)-[:HAS_SECTION]->(sec)
            MERGE (sec)-[:HAS_CHAPTER]->(c)
            DELETE r_doc
            RETURN count(DISTINCT sec) AS created_sec_count
            """, doc_id=doc_element_id, sec_id=sec_id, sec_name=sec_name, c_ids=group_chapter_ids).single()

            if result and result["created_sec_count"] > 0:
                valid_sec_count += 1

        print(f"[{self.database}] Document 《{doc_title}》 处理完成，成功根据 Slide 聚类划分出 {valid_sec_count} 个 Section。")

    def run_by_name(self, doc_name: str):
        with self.driver.session(database=self.database) as session:
            doc = session.run(
                "MATCH (d:Document {name: $name}) RETURN elementId(d) AS id",
                name=doc_name
            ).single()

            if not doc:
                print(f"[{self.database}] 未找到 name 为 《{doc_name}》 的 Document 节点。")
                return

            self.process_document(session, doc['id'], doc_name)


if __name__ == "__main__":
    if not NEO4J_PASSWORD:
        raise ValueError("请在 .env 文件中设置 NEO4J_PASSWORD 环境变量！")

    target_name = input("请输入要划分 Section 的 Document Name: ").strip()

    clusterer = DocumentSectionClustererGDS(
        uri=NEO4J_URI,
        auth=(NEO4J_USER, NEO4J_PASSWORD),
        database=NEO4J_DATABASE
    )
    try:
        clusterer.run_by_name(target_name)
    finally:
        clusterer.close()