import os
import json
import re
from dotenv import load_dotenv
from openai import OpenAI

# 加载环境变量
load_dotenv()
LITELLM_PROXY_API_BASE = os.getenv("LITELLM_PROXY_API_BASE")
LITELLM_PROXY_API_KEY = os.getenv("LITELLM_PROXY_API_KEY")

# 初始化 LLM 客户端 (适配 LiteLLM Proxy)
llm_client = OpenAI(
    base_url=LITELLM_PROXY_API_BASE,
    api_key=LITELLM_PROXY_API_KEY
)

def extract_clause_between_period_and_comma(audio_text: str) -> list[str]:
    """
    步骤 ①：从 audio 字段中摘取从句号 '。' 到逗号 '，'（含英文逗号 ','）之间的文本片段
    """
    if not audio_text or not isinstance(audio_text, str):
        return []
    # 正则提取从 '。' 开始到 '，' 或 ',' 结束的语句片段
    matches = re.findall(r'。(.*?)[，,]', audio_text)
    return [m.strip() for m in matches if m.strip()]

def get_questions_from_llm(snippets: list[str], model_name: str = "gpt-4o-mini") -> list[str]:
    """
    步骤 ②：拼接片段发送给 LLM，提取 0~3 个 15 字以内的有价值短问题
    """
    if not snippets:
        return []

    combined_text = "\n".join(f"- {s}" for s in snippets)
    
    prompt = f"""请分析以下从课程音频中提取出的口语文本片段，提炼或总结出 0~3 个最有知识价值的短问题。

要求：
1. 问题数量：0 到 3 个。如果文本只是口头禅、打招呼或过渡句，无实际知识价值，请返回空列表 []。
2. 字数限制：每个问题必须控制在 15 个字以内（含标点符号）。
3. 输出格式：必须严格返回 JSON 字符串数组格式，例如：["强连通分量如何定义？", "DFS的时间复杂度是多少？"]。不要包含任何多余的前言、后记或 Markdown 说明。

待分析文本片段：
{combined_text}
"""

    try:
        response = llm_client.chat.completions.create(
            model=model_name,
            messages=[
                {"role": "system", "content": "你是一个教学内容分析助手，善于从口语化课程提炼核心问题。请严格按要求输出 JSON 列表。"},
                {"role": "user", "content": prompt}
            ],
            temperature=0.2
        )
        content = response.choices[0].message.content.strip()
        
        # 去除 Markdown 代码块包裹符（若有）
        if content.startswith("```"):
            content = re.sub(r"^```[a-zA-Z]*\n?", "", content)
            content = re.sub(r"\n?```$", "", content)

        questions = json.loads(content)
        if isinstance(questions, list):
            # 二次校验：确保格式为字符串且字数在15字以内
            valid_qs = [q for q in questions if isinstance(q, str) and len(q) <= 15]
            return valid_qs[:3]
        return []
    except Exception as e:
        print(f"  [LLM 处理失败]: {e}")
        return []

def process_json_folder(folder_path: str, model_name: str = "gpt-4o-mini"):
    """
    遍历文件夹下的所有 JSON 文件并执行更新写回
    """
    if not os.path.exists(folder_path):
        print(f"错误: 文件夹路径 '{folder_path}' 不存在！")
        return

    json_files = [f for f in os.listdir(folder_path) if f.endswith('.json')]
    print(f"共找到 {len(json_files)} 个 JSON 文件，开始处理...\n")

    for file_name in json_files:
        file_path = os.path.join(folder_path, file_name)
        print(f"正在处理: {file_name}")

        try:
            with open(file_path, 'r', encoding='utf-8') as f:
                data = json.load(f)

            if isinstance(data, list):
                for page in data:
                    audio_text = page.get("audio", "")
                    
                    # ① 摘取从句号到逗号的片段
                    snippets = extract_clause_between_period_and_comma(audio_text)
                    
                    # ② 拼接片段并调用 LLM 提取 0~3 个短问题
                    questions = get_questions_from_llm(snippets, model_name=model_name)
                    
                    # ③ 写进字段 "question"
                    page["question"] = questions
                    print(f"  - 第 {page.get('page_idx', '?')} 页生成问题: {questions}")

                # 写回原 JSON 文件
                with open(file_path, 'w', encoding='utf-8') as f:
                    json.dump(data, f, ensure_ascii=False, indent=4)
                print(f"✓ 已更新文件: {file_name}\n")
            else:
                print(f"⚠ 跳过 {file_name}：根节点不是 List 结构。\n")

        except Exception as e:
            print(f"✕ 处理文件 {file_name} 时发生错误: {e}\n")

if __name__ == "__main__":
    target_dir = input("请输入存放 JSON 文件的文件夹路径: ").strip()
    process_json_folder(target_dir, model_name="deepseek-chat")