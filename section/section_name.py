import os
from typing import List
from dotenv import load_dotenv
from neo4j import GraphDatabase
from openai import OpenAI

# 1. 加载环境变量
load_dotenv()

NEO4J_URI = os.getenv("NEO4J_URI", "bolt://localhost:7687")
NEO4J_USER = os.getenv("NEO4J_USER", "neo4j")
NEO4J_PASSWORD = os.getenv("NEO4J_PASSWORD")
NEO4J_DATABASE = os.getenv("NEO4J_DATABASE", "0801")

LITELLM_PROXY_API_BASE = os.getenv("LITELLM_PROXY_API_BASE")
LITELLM_PROXY_API_KEY = os.getenv("LITELLM_PROXY_API_KEY")
MODEL_NAME = os.getenv("LLM_MODEL_NAME", "deepseek-chat")

# 初始化 OpenAI 客户端连接 LiteLLM Proxy
llm_client = OpenAI(
    api_key=LITELLM_PROXY_API_KEY,
    base_url=LITELLM_PROXY_API_BASE
)


def generate_section_title_with_llm(chapter_titles: List[str]) -> str:
    """
    调用 LLM 根据 Section 下属所有 Chapter 的标题总结生成 Section 标题
    """
    if not chapter_titles:
        return "未命名小节"
    
    titles_str = "\n".join([f"- {t}" for t in chapter_titles if t])
    prompt = (
        f"以下是一个文档/PPT小节（Section）下包含的所有章节（Chapter）标题列表：\n"
        f"{titles_str}\n\n"
        f"请根据上述章节内容，归纳总结出一个 4 到 8 个字的简短小节标题（Section Title）。\n"
        f"要求：直接输出总结后的标题文本，不要包含任何标点符号、解释性文字或多余符号。"
    )

    try:
        response = llm_client.chat.completions.create(
            model=MODEL_NAME,
            messages=[
                {"role": "system", "content": "你是一个专业的文档与演示文稿结构分析专家，擅长概括章节主题。"},
                {"role": "user", "content": prompt}
            ],
            temperature=0.1
        )
        sec_title = response.choices[0].message.content.strip()
        # 清理可能包含的标点或多余换行
        sec_title = sec_title.replace('"', '').replace("'", "").replace("。", "")
        return sec_title if sec_title else "未命名小节"
    except Exception as e:
        print(f"[LLM 调用异常]: {e}")
        # LLM 异常时的兜底命名：取第一个 Chapter 的标题
        return chapter_titles[0] if chapter_titles else "未命名小节"


class SectionTitleUpdater:
    def __init__(self, uri: str, auth: tuple, database: str = "neo4j"):
        self.driver = GraphDatabase.driver(uri, auth=auth)
        self.database = database

    def close(self):
        self.driver.close()

    def update_all_section_titles(self):
        """
        全流程：
        1. 查出所有 Section 及其包含的 Chapter 的 title 列表
        2. 调用 LLM 生成新的 Section title
        3. 写回 Neo4j 数据库中的 Section.title 属性
        """
        with self.driver.session(database=self.database) as session:
            # 1. 查询 Section 节点及其关联的 Chapter 节点标题
            query_sections = """
            MATCH (sec:Section)-[:HAS_CHAPTER]->(c:Chapter)
            RETURN elementId(sec) AS sec_element_id, 
                   sec.id AS sec_custom_id,
                   sec.name AS current_title,
                   collect(c.name) AS chapter_titles
            """
            sections_data = session.run(query_sections).data()

            print(f"找到 {len(sections_data)} 个 Section 节点，开始生成动态标题...\n")

            for sec in sections_data:
                sec_element_id = sec["sec_element_id"]
                current_title = sec["current_title"]
                chapter_titles = sec["chapter_titles"]

                # 2. 调用 LLM 提炼/生成新的标题
                new_sec_title = generate_section_title_with_llm(chapter_titles)

                print(f"Section ID: {sec['sec_custom_id']}")
                print(f"  └─ 下属 Chapters: {chapter_titles}")
                print(f"  └─ 原 Title: {current_title}")
                print(f"  └─ 新生成的 LLM Title: {new_sec_title}\n")

                # 3. 将 LLM 生成的 title 更新回 Neo4j 的 Section 节点
                update_query = """
                MATCH (sec:Section) 
                WHERE elementId(sec) = $sec_id
                SET sec.name = $new_title
                """
                session.run(update_query, sec_id=sec_element_id, new_title=new_sec_title)

            print("所有 Section 标题更新完毕！")


if __name__ == "__main__":
    if not NEO4J_PASSWORD:
        raise ValueError("请在 .env 文件中设置 NEO4J_PASSWORD 环境变量！")
    if not LITELLM_PROXY_API_KEY or not LITELLM_PROXY_API_BASE:
        raise ValueError("请在 .env 文件中配置 LITELLM_PROXY_API_KEY 和 LITELLM_PROXY_API_BASE！")

    updater = SectionTitleUpdater(
        uri=NEO4J_URI,
        auth=(NEO4J_USER, NEO4J_PASSWORD),
        database=NEO4J_DATABASE
    )
    
    try:
        updater.update_all_section_titles()
    finally:
        updater.close()