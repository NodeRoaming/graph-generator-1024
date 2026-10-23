import os
import json
import re
from dotenv import load_dotenv
from openai import OpenAI
from jinja2 import Environment, FileSystemLoader

# 1. 加载环境变量与配置
load_dotenv()
LITELLM_PROXY_API_BASE = os.getenv("LITELLM_PROXY_API_BASE")
LITELLM_PROXY_API_KEY = os.getenv("LITELLM_PROXY_API_KEY")

# 初始化 LLM 客户端
llm_client = OpenAI(
    base_url=LITELLM_PROXY_API_BASE,
    api_key=LITELLM_PROXY_API_KEY
)

# 初始化 Jinja2 模板环境
jinja_env = Environment(loader=FileSystemLoader(searchpath="."))

def extract_entities_from_text(page_text: str, chunk_id: str, prompt_template_name: str = "prompt-med.j2", model_name: str = "deepseek-chat") -> list[dict]:
    """
    引用 prompt-med.j2 模板，将 text 字段输入 LLM 解析并返回附带 chunk_id 的实体 JSON 列表
    """
    if not page_text or not page_text.strip():
        return []

    try:
        # 1. 加载并渲染 Jinja2 模板
        template = jinja_env.get_template(prompt_template_name)
        prompt_content = template.render(text=page_text)

        # 2. 调用 LLM
        response = llm_client.chat.completions.create(
            model=model_name,
            messages=[
                {"role": "system", "content": "你是一个医学与健康知识图谱实体抽取专家。请严格按照要求的 JSON 数组格式输出抽取出的实体列表。"},
                {"role": "user", "content": prompt_content}
            ],
            temperature=0.1
        )
        content = response.choices[0].message.content.strip()

        # 3. 清理 Markdown 代码块标记（若存在）
        if content.startswith("```"):
            content = re.sub(r"^```[a-zA-Z]*\n?", "", content)
            content = re.sub(r"\n?```$", "", content)

        # 4. 解析为 JSON 对象/列表，并在每个实体前加入 chunk_id 字段
        entities = json.loads(content)
        if isinstance(entities, list):
            formatted_entities = []
            for item in entities:
                if isinstance(item, dict):
                    # 构造新的字典，确保 chunk_id 在最前面
                    entity_with_chunk = {"chunk_id": chunk_id, **item}
                    formatted_entities.append(entity_with_chunk)
            return formatted_entities
        return []

    except Exception as e:
        print(f"  [Chunk {chunk_id} 实体抽取失败]: {e}")
        return []

def process_single_json_file(json_file_path: str, model_name: str = "deepseek-chat"):
    """
    处理指定的 JSON 文件，抽取实体并将包含 chunk_id 的所有实体展平写进 XX-entity.json
    """
    if not os.path.exists(json_file_path):
        print(f"错误: 文件 '{json_file_path}' 不存在！")
        return

    if not json_file_path.endswith('.json'):
        print(f"错误: '{json_file_path}' 不是一个有效的 .json 文件！")
        return

    print(f"正在读取并处理文件: {json_file_path}")

    try:
        with open(json_file_path, 'r', encoding='utf-8') as f:
            data = json.load(f)

        if not isinstance(data, list):
            print(f"错误: 文件 {json_file_path} 的根节点不是 List 结构。")
            return

        all_chunk_entities = []

        # 逐个 chunk 读取并进行实体抽取
        for item in data:
            chunk_id = item.get("chunk_id", "")
            chunk_text = item.get("text", "")

            print(f"  - 正在处理 chunk: {chunk_id}...")
            
            # 调用 LLM 根据 text 抽取实体列表（自动添加对应 chunk 的 chunk_id）
            entities = extract_entities_from_text(chunk_text, chunk_id=chunk_id, prompt_template_name="prompt-med.j2", model_name=model_name)
            
            # 展平追加到主列表
            all_chunk_entities.extend(entities)

        # 构建新的输出文件名：XX.json -> XX-entity.json
        base, ext = os.path.splitext(json_file_path)
        output_file_path = f"{base}-entity.json"

        # 写回新文件
        with open(output_file_path, 'w', encoding='utf-8') as f:
            json.dump(all_chunk_entities, f, ensure_ascii=False, indent=4)

        print(f"\n✓ 实体抽取完成！结果已成功保存至: {output_file_path}")

    except Exception as e:
        print(f"✕ 处理文件发生异常: {e}")

if __name__ == "__main__":
    file_path = input("请输入完整的 JSON 文件路径 (例如 path/to/XX.json): ").strip()
    process_single_json_file(file_path, model_name="deepseek-chat")