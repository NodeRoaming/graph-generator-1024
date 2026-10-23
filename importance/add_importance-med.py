import csv
import json
import os
from dotenv import load_dotenv
from neo4j import GraphDatabase
from openai import OpenAI
from zhipuai import ZhipuAI

# 加载环境变量
load_dotenv()

ZHIPU_API_KEY = os.getenv("ZHIPU_API_KEY")
NEO4J_URI = os.getenv("NEO4J_URI", "neo4j://127.0.0.1:7687")
NEO4J_USERNAME = os.getenv("NEO4J_USERNAME", "neo4j")
NEO4J_PASSWORD = os.getenv("NEO4J_PASSWORD", "neo4j")
NEO4J_DATABASE = os.getenv("NEO4J_DATABASE", "neo4j")

# LiteLLM Proxy 配置
LITELLM_PROXY_API_BASE = os.getenv("LITELLM_PROXY_API_BASE")
LITELLM_PROXY_API_KEY = os.getenv("LITELLM_PROXY_API_KEY")
LLM_MODEL = os.getenv("LLM_MODEL", "deepseek-chat")

# 初始化客户端
zhipu_client = ZhipuAI(api_key=ZHIPU_API_KEY)
llm_client = OpenAI(
    base_url=LITELLM_PROXY_API_BASE, api_key=LITELLM_PROXY_API_KEY
)
driver = GraphDatabase.driver(
    NEO4J_URI, auth=(NEO4J_USERNAME, NEO4J_PASSWORD)
)

INPUT_CSV = "extracted_keywords.csv"
ENTITY_NAME_PROP = "name"


def get_zhipu_embedding(text):
    """调用智谱 AI 生成向量"""
    try:
        response = zhipu_client.embeddings.create(
            model="embedding-2", input=[text]
        )
        return response.data[0].embedding
    except Exception as e:
        print(f"❌ 生成 Embedding 失败 [{text}]: {e}")
        return None


def search_top_k_entities_brute_force(session, embedding_vec, k=3):
    """暴力计算：直接遍历所有 Entity 计算余弦相似度（不建索引）"""
    cypher = f"""
    MATCH (e:Entity)
    WHERE e.embedding IS NOT NULL
    WITH e, vector.similarity.cosine(e.embedding, $embedding) AS score
    ORDER BY score DESC
    LIMIT $k
    RETURN elementId(e) as node_id, e.{ENTITY_NAME_PROP} as name, score
    """
    results = session.run(
        cypher,
        k=k,
        embedding=embedding_vec,
        database_=NEO4J_DATABASE,
    )
    return [
        {
            "node_id": record["node_id"],
            "name": record["name"],
            "score": record["score"],
        }
        for record in results
    ]


def increment_entity_importance(session, node_id):
    """为指定节点的 importance 属性 +1"""
    cypher = """
    MATCH (e) WHERE elementId(e) = $node_id
    SET e.importance = coalesce(e.importance, 0) + 1
    RETURN e.importance as new_importance
    """
    result = session.run(
        cypher, node_id=node_id, database_=NEO4J_DATABASE
    ).single()
    return result["new_importance"] if result else None


def judge_synonym_with_llm(keyword, candidates):
    """调用 LLM 判断候选实体中是否有与关键词相同含义的实体"""
    candidate_names = [c["name"] for c in candidates if c.get("name")]
    if not candidate_names:
        return None

    prompt = f"""你是一个专业医学/学术知识图谱判定专家。请判断在给出的“候选实体列表”中，是否存在与“目标词汇”意思相同或指代同一概念/同义词的实体。

目标词汇: {keyword}
候选实体列表: {json.dumps(candidate_names, ensure_ascii=False)}

要求：
1. 如果存在同义/同概念的实体，请直接输出候选列表中【完全一致的实体名称】。
2. 如果都不相同或只是上下级关系而非同义概念，请输出 "NONE"。
3. 不要包含任何解释或额外字符。
"""

    try:
        response = llm_client.chat.completions.create(
            model=LLM_MODEL,
            messages=[
                {
                    "role": "system",
                    "content": "你是一个严谨的语义判定助手，严格按要求输出。",
                },
                {"role": "user", "content": prompt},
            ],
            temperature=0.0,
        )
        matched_name = response.choices[0].message.content.strip()

        for candidate in candidates:
            if candidate["name"] == matched_name:
                return candidate["node_id"]
        return None
    except Exception as e:
        print(f"⚠️ LLM 同义词判定出错: {e}")
        return None


def process_keyword(session, keyword):
    """处理单个关键词的匹配与更新逻辑"""
    keyword = keyword.strip()
    if not keyword:
        return

    print(f"\n🔍 正在处理关键词: [{keyword}]")

    # 1. 获取关键词 Embedding
    embedding_vec = get_zhipu_embedding(keyword)
    if not embedding_vec:
        return

    # 2. 暴力计算：获取余弦相似度最高的前 3 个实体
    candidates = search_top_k_entities_brute_force(session, embedding_vec, k=3)
    if not candidates:
        print(f"  └─ Neo4j 中未匹配到任何实体。")
        return

    # 3. 检查是否存在完全相同的词汇
    exact_match = next(
        (c for c in candidates if c["name"] and c["name"].strip() == keyword),
        None,
    )

    matched_node_id = None

    if exact_match:
        matched_node_id = exact_match["node_id"]
        print(f"  ├─ ✨ 命中完全相同的实体: '{exact_match['name']}'")
    else:
        # 4. 若无完全相同的词汇，调用 LLM 进行同义词判定
        candidate_names = [c["name"] for c in candidates]
        print(
            f"  ├─ 未发现完全同名实体，调用 LLM 进行语义匹配。候选列表: {candidate_names}"
        )
        matched_node_id = judge_synonym_with_llm(keyword, candidates)
        if matched_node_id:
            matched_candidate = next(
                c for c in candidates if c["node_id"] == matched_node_id
            )
            print(f"  ├─ 🤖 LLM 判定同义实体: '{matched_candidate['name']}'")
        else:
            print(f"  ├─ ❌ LLM 判定候选实体中均无相同含义概念。")

    # 5. 给对应的 Entity 节点 importance 属性 +1
    if matched_node_id:
        new_val = increment_entity_importance(session, matched_node_id)
        print(f"  └─ 📈 实体 importance 属性更新为: {new_val}")


def main():
    if not os.path.exists(INPUT_CSV):
        print(f"❌ 找不到 CSV 文件 '{INPUT_CSV}'！")
        return

    with driver.session(database=NEO4J_DATABASE) as session:
        with open(INPUT_CSV, mode="r", encoding="utf-8-sig") as f:
            reader = csv.DictReader(f)

            for row in reader:
                keywords_str = row.get("keywords", "")
                if not keywords_str:
                    continue

                keywords = [
                    kw.strip() for kw in keywords_str.split(",") if kw.strip()
                ]

                for kw in keywords:
                    process_keyword(session, kw)

    driver.close()
    print("\n==================================================")
    print("✅ 所有关键词处理完成，Neo4j 节点 importance 属性已更新！")
    print("==================================================")


if __name__ == "__main__":
    main()