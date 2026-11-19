# coding=utf-8
"""
AI 智能筛选模块

通过 AI 对新闻进行标签分类：
1. 阶段 A：从用户兴趣描述中提取结构化标签
2. 阶段 B：对新闻标题按标签进行批量分类
"""

import hashlib
import json
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Callable, Dict, List, Optional

from trendradar.ai.client import AIClient
from trendradar.ai.prompt_loader import load_prompt_template


@dataclass
class AIFilterResult:
    """AI 筛选结果，传给报告和通知模块"""
    tags: List[Dict] = field(default_factory=list)
    # [{"tag": str, "description": str, "count": int, "items": [
    #     {"title": str, "source_id": str, "source_name": str,
    #      "url": str, "mobile_url": str, "rank": int, "ranks": [...],
    #      "first_time": str, "last_time": str, "count": int,
    #      "relevance_score": float, "source_type": str}
    # ]}]
    total_matched: int = 0       # 匹配新闻总数
    total_processed: int = 0     # 处理新闻总数
    success: bool = False
    error: str = ""


class AIFilter:
    """AI 智能筛选器"""

    def __init__(
        self,
        ai_config: Dict[str, Any],
        filter_config: Dict[str, Any],
        get_time_func: Callable,
        debug: bool = False,
    ):
        self.client = AIClient(ai_config)
        self.filter_config = filter_config
        self.batch_size = filter_config.get("BATCH_SIZE", 200)
        self.get_time_func = get_time_func
        self.debug = debug

        # 加载提示词模板
        self.classify_system, self.classify_user = load_prompt_template(
            filter_config.get("PROMPT_FILE", "ai_filter_prompt.txt"),
            config_subdir="ai_filter", label="AI Filter",
        )
        self.extract_system, self.extract_user = load_prompt_template(
            filter_config.get("EXTRACT_PROMPT_FILE", "ai_filter_extract_prompt.txt"),
            config_subdir="ai_filter", label="AI Filter",
        )
        self.update_tags_system, self.update_tags_user = load_prompt_template(
            filter_config.get("UPDATE_TAGS_PROMPT_FILE", "update_tags_prompt.txt"),
            config_subdir="ai_filter", label="AI Filter",
        )

    def compute_interests_hash(self, interests_content: str, filename: str = "ai_interests.txt") -> str:
        """计算兴趣描述的 hash，格式为 filename:md5"""
        # 去除前后空白和注释行，确保内容变化才改变 hash
        lines = []
        for line in interests_content.strip().splitlines():
            line = line.strip()
            if line and not line.startswith("#"):
                lines.append(line)
        normalized = "\n".join(lines)
        content_hash = hashlib.md5(normalized.encode("utf-8")).hexdigest()
        return f"{filename}:{content_hash}"

    def load_interests_content(self, interests_file: Optional[str] = None) -> Optional[str]:
        """加载兴趣描述文件内容

        解析逻辑：
        - interests_file 为 None：使用默认 config/ai_interests.txt
        - interests_file 有值：仅查 config/custom/ai/{filename}

        注意：调用方（context.py）已完成 config/timeline 的合并决策，
        此处不再二次读取 filter_config，避免语义冲突。
        """
        config_dir = Path(__file__).parent.parent.parent / "config"
        configured_file = interests_file

        if configured_file:
            # 自定义兴趣文件：仅查 custom/ai 目录
            filename = configured_file
            interests_path = config_dir / "custom" / "ai" / filename
            if not interests_path.exists():
                print(f"[AI Filter] Custom interests file does not exist: {filename}")
                print(f"[AI Filter]   Searched: {interests_path}")
                return None
        else:
            # 默认兴趣文件：固定使用 config/ai_interests.txt
            filename = "ai_interests.txt"
            interests_path = config_dir / filename
            if not interests_path.exists():
                print(f"[AI Filter] Default interests file does not exist: {filename}")
                print(f"[AI Filter]   Searched: {interests_path}")
                return None

        if not interests_path.exists():
            print(f"[AI Filter] Interests file does not exist: {interests_path}")
            return None

        content = interests_path.read_text(encoding="utf-8").strip()
        if not content:
            print("[AI Filter] Interests file is empty")
            return None

        return content

    def extract_tags(self, interests_content: str) -> List[Dict]:
        """
        阶段 A：从兴趣描述中提取结构化标签

        Args:
            interests_content: 用户的兴趣描述文本

        Returns:
            [{"tag": str, "description": str}, ...]
        """
        if not self.extract_user:
            print("[AI Filter] Tag extraction prompt is empty")
            return []

        user_prompt = self.extract_user.replace("{interests_content}", interests_content)

        messages = []
        if self.extract_system:
            messages.append({"role": "system", "content": self.extract_system})
        messages.append({"role": "user", "content": user_prompt})

        if self.debug:
            print(f"\n[AI Filter][DEBUG] === Tag extraction prompt ===")
            for m in messages:
                print(f"[{m['role']}]\n{m['content']}")
            print(f"[AI Filter][DEBUG] === End of prompt ===")

        try:
            response = self.client.chat(messages)

            if self.debug:
                print(f"\n[AI Filter][DEBUG] === Raw AI tag extraction response ===")
                # 尝试格式化 JSON 便于阅读
                self._print_formatted_json(response)
                print(f"[AI Filter][DEBUG] === End of response ===")

            tags = self._parse_tags_response(response)
            print(f"[AI Filter] Extracted {len(tags)} tags")
            for t in tags:
                print(f"   {t['tag']}: {t.get('description', '')}")

            if self.debug:
                json_str = self._extract_json(response)
                if not json_str:
                    print(f"[AI Filter][DEBUG] Could not extract JSON from the response")
                else:
                    raw_data = json.loads(json_str)
                    raw_tags = raw_data.get("tags", [])
                    skipped = len(raw_tags) - len(tags)
                    if skipped > 0:
                        print(f"[AI Filter][DEBUG] Raw tags: {len(raw_tags)}; valid: {len(tags)}; skipped: {skipped} (missing tag field or invalid format)")

            return tags
        except json.JSONDecodeError as e:
            print(f"[AI Filter] Tag extraction failed: JSON parse error: {e}")
            if self.debug:
                print(f"[AI Filter][DEBUG] JSON content passed to the parser: {self._extract_json(response) if response else '(empty response)'}")
            return []
        except Exception as e:
            print(f"[AI Filter] Tag extraction failed: {type(e).__name__}: {e}")
            return []

    def update_tags(self, old_tags: List[Dict], interests_content: str) -> Optional[Dict]:
        """
        阶段 A'：AI 对比旧标签和新兴趣描述，给出更新方案

        Args:
            old_tags: [{"tag": str, "description": str, "id": int}, ...]
            interests_content: 新的兴趣描述文本

        Returns:
            {"keep": [{"tag": str, "description": str}],
             "add": [{"tag": str, "description": str}],
             "remove": [str],
             "change_ratio": float}
            失败返回 None
        """
        if not self.update_tags_user:
            print("[AI Filter] Tag update prompt is empty; extracting tags again")
            return None

        # 构造旧标签 JSON
        old_tags_json = json.dumps(
            [{"tag": t["tag"], "description": t.get("description", "")} for t in old_tags],
            ensure_ascii=False, indent=2
        )

        user_prompt = self.update_tags_user.replace(
            "{old_tags_json}", old_tags_json
        ).replace(
            "{interests_content}", interests_content
        )

        messages = []
        if self.update_tags_system:
            messages.append({"role": "system", "content": self.update_tags_system})
        messages.append({"role": "user", "content": user_prompt})

        if self.debug:
            print(f"\n[AI Filter][DEBUG] === Tag update prompt ===")
            for m in messages:
                print(f"[{m['role']}]\n{m['content']}")
            print(f"[AI Filter][DEBUG] === End of prompt ===")

        try:
            response = self.client.chat(messages)

            if self.debug:
                print(f"\n[AI Filter][DEBUG] === Raw AI tag update response ===")
                self._print_formatted_json(response)
                print(f"[AI Filter][DEBUG] === End of response ===")

            result = self._parse_update_tags_response(response)
            if result is None:
                return None

            keep_count = len(result.get("keep", []))
            add_count = len(result.get("add", []))
            remove_count = len(result.get("remove", []))
            ratio = result.get("change_ratio", 0)
            print(f"[AI Filter] AI tag update: keep {keep_count}, add {add_count}, remove {remove_count}, change_ratio={ratio:.2f}")

            return result
        except Exception as e:
            print(f"[AI Filter] Tag update failed: {type(e).__name__}: {e}")
            return None

    def _parse_update_tags_response(self, response: str) -> Optional[Dict]:
        """解析标签更新的 AI 响应"""
        json_str = self._extract_json(response)
        if not json_str:
            print("[AI Filter] Could not extract JSON from the tag update response")
            return None

        data = json.loads(json_str)

        # 校验必需字段
        keep = data.get("keep", [])
        add = data.get("add", [])
        remove = data.get("remove", [])
        change_ratio = float(data.get("change_ratio", 0))

        # 校验 keep/add 格式
        validated_keep = []
        for t in keep:
            if isinstance(t, dict) and "tag" in t:
                validated_keep.append({
                    "tag": str(t["tag"]).strip(),
                    "description": str(t.get("description", "")).strip(),
                })

        validated_add = []
        for t in add:
            if isinstance(t, dict) and "tag" in t:
                validated_add.append({
                    "tag": str(t["tag"]).strip(),
                    "description": str(t.get("description", "")).strip(),
                })

        validated_remove = [str(r).strip() for r in remove if r]

        # change_ratio 限制在 0~1
        change_ratio = max(0.0, min(1.0, change_ratio))

        return {
            "keep": validated_keep,
            "add": validated_add,
            "remove": validated_remove,
            "change_ratio": change_ratio,
        }

    def _parse_tags_response(self, response: str) -> List[Dict]:
        """解析标签提取的 AI 响应"""
        json_str = self._extract_json(response)
        if not json_str:
            return []

        data = json.loads(json_str)
        tags_raw = data.get("tags", [])

        tags = []
        for t in tags_raw:
            if not isinstance(t, dict) or "tag" not in t:
                continue
            tags.append({
                "tag": str(t["tag"]).strip(),
                "description": str(t.get("description", "")).strip(),
            })

        return tags

    def classify_batch(
        self,
        titles: List[Dict],
        tags: List[Dict],
        interests_content: str = "",
    ) -> Optional[List[Dict]]:
        """
        阶段 B：对一批新闻标题做分类

        Args:
            titles: [{"id": news_item_id, "title": str, "source": str}]
            tags: [{"id": tag_id, "tag": str, "description": str}]
            interests_content: 用户的兴趣描述（含质量过滤要求）

        Returns:
            成功返回 [{"news_item_id": int, "tag_id": int, "relevance_score": float}, ...]（无匹配时为空列表）；
            调用失败返回 None（用于区分"无匹配"与"调用失败"，失败批次不标记已分析以便下次重试）
        """
        if not titles or not tags:
            return []

        if not self.classify_user:
            print("[AI Filter] Classification prompt is empty")
            return None

        # 构建标签列表文本
        tags_list = "\n".join(
            f"{t['id']}. {t['tag']}: {t.get('description', '')}"
            for t in tags
        )

        # 构建新闻列表文本
        news_list = "\n".join(
            f"{t['id']}. [{t.get('source', '')}] {t['title']}"
            for t in titles
        )

        # 填充模板
        user_prompt = self.classify_user
        user_prompt = user_prompt.replace("{interests_content}", interests_content)
        user_prompt = user_prompt.replace("{tags_list}", tags_list)
        user_prompt = user_prompt.replace("{news_count}", str(len(titles)))
        user_prompt = user_prompt.replace("{news_list}", news_list)

        messages = []
        if self.classify_system:
            messages.append({"role": "system", "content": self.classify_system})
        messages.append({"role": "user", "content": user_prompt})

        if self.debug:
            print(f"\n[AI Filter][DEBUG] === Classification prompt (headlines={len(titles)}, tags={len(tags)}) ===")
            for m in messages:
                role = m['role']
                content = m['content']
                # 截断过长的新闻列表：只显示前5条和后5条
                lines = content.split('\n')
                # 找到新闻列表区域并截断
                if len(lines) > 30:
                    # 显示前15行 + 省略提示 + 后10行
                    head = lines[:15]
                    tail = lines[-10:]
                    omitted = len(lines) - 25
                    truncated = '\n'.join(head) + f'\n... (Omitted {omitted} lines) ...\n' + '\n'.join(tail)
                    print(f"[{role}]\n{truncated}")
                else:
                    print(f"[{role}]\n{content}")
            print(f"[AI Filter][DEBUG] === End of prompt (length: {sum(len(m['content']) for m in messages)} characters) ===")

        try:
            response = self.client.chat(messages)

            return self._parse_classify_response(response, titles, tags)
        except Exception as e:
            print(f"[AI Filter] Classification request failed: {type(e).__name__}: {e}")
            return None

    def _parse_classify_response(
        self,
        response: str,
        titles: List[Dict],
        tags: List[Dict],
    ) -> List[Dict]:
        """解析分类的 AI 响应

        支持两种 JSON 格式：
        - 新格式（扁平）: [{"id": 1, "tag_id": 1, "score": 0.9}, ...]
        - 旧格式（嵌套）: [{"id": 1, "tags": [{"tag_id": 1, "score": 0.9}]}, ...]

        每条新闻只保留一个最高分的 tag，杜绝同一条出现在多个标签下。
        """
        json_str = self._extract_json(response)
        if not json_str:
            if self.debug:
                print(f"[AI Filter][DEBUG] Could not extract JSON from the classification response; first 500 characters: {(response or '')[:500]}")
            return []

        try:
            data = json.loads(json_str)
        except json.JSONDecodeError as e:
            if self.debug:
                print(f"[AI Filter][DEBUG] Classification response JSON parsing failed: {e}")
                print(f"[AI Filter][DEBUG] First 500 characters of the extracted JSON: {json_str[:500]}")
            return []

        if not isinstance(data, list):
            if self.debug:
                print(f"[AI Filter][DEBUG] Classification response must be an array; received: {type(data).__name__}")
            return []

        # 构建 id 映射
        title_ids = {t["id"] for t in titles}
        title_map = {t["id"]: t["title"] for t in titles}
        tag_id_set = {t["id"] for t in tags}
        tag_name_map = {t["id"]: t["tag"] for t in tags}

        # 每条新闻只保留一个最高分的 tag
        best_per_news: Dict[int, Dict] = {}  # news_id -> {"tag_id": ..., "score": ...}
        skipped_news_ids = 0
        skipped_tag_ids = 0
        skipped_empty = 0

        for item in data:
            if not isinstance(item, dict):
                continue
            news_id = item.get("id")
            if news_id not in title_ids:
                skipped_news_ids += 1
                continue

            # 收集此条新闻的所有候选 tag
            candidates = []

            if "tag_id" in item:
                # 新格式（扁平）: {"id": 1, "tag_id": 1, "score": 0.9}
                candidates.append({"tag_id": item["tag_id"], "score": item.get("score", 0.5)})
            elif "tags" in item:
                # 旧格式（嵌套）: {"id": 1, "tags": [{"tag_id": 1, "score": 0.9}]}
                matched_tags = item.get("tags", [])
                if isinstance(matched_tags, list):
                    if not matched_tags:
                        skipped_empty += 1
                        continue
                    candidates.extend(matched_tags)

            if not candidates:
                skipped_empty += 1
                continue

            # 取最高分的有效 tag
            best_tag_id = None
            best_score = -1.0

            for tag_match in candidates:
                if not isinstance(tag_match, dict):
                    continue
                tag_id = tag_match.get("tag_id")
                if tag_id not in tag_id_set:
                    skipped_tag_ids += 1
                    continue

                score = tag_match.get("score", 0.5)
                try:
                    score = float(score)
                    score = max(0.0, min(1.0, score))
                except (ValueError, TypeError):
                    score = 0.5

                if score > best_score:
                    best_score = score
                    best_tag_id = tag_id

            if best_tag_id is not None:
                # 如果同一条新闻被多次返回，只保留分数更高的
                existing = best_per_news.get(news_id)
                if existing is None or best_score > existing["relevance_score"]:
                    best_per_news[news_id] = {
                        "news_item_id": news_id,
                        "tag_id": best_tag_id,
                        "relevance_score": best_score,
                    }

        results = list(best_per_news.values())

        if self.debug:
            ai_returned = len(data)
            print(f"[AI Filter][DEBUG] --- Parsed classification results ---")
            print(f"[AI Filter][DEBUG] AI returned {ai_returned} items; valid: {len(results)} items (only the highest-scoring tag is kept for each article)")
            if skipped_empty > 0:
                print(f"[AI Filter][DEBUG] Skipped empty tags: {skipped_empty} items")
            if skipped_news_ids > 0:
                print(f"[AI Filter][DEBUG] !! Skipped invalid news_id: {skipped_news_ids} items")
            if skipped_tag_ids > 0:
                print(f"[AI Filter][DEBUG] !! Skipped invalid tag_id: {skipped_tag_ids} items")

            # 按标签汇总
            tag_summary: Dict[int, List[str]] = {}
            for r in results:
                tid = r["tag_id"]
                if tid not in tag_summary:
                    tag_summary[tid] = []
                tag_summary[tid].append(
                    f"  [{r['news_item_id']}] {title_map.get(r['news_item_id'], '?')[:40]} (score={r['relevance_score']:.2f})"
                )

            for tid, items in tag_summary.items():
                tname = tag_name_map.get(tid, f"tag_{tid}")
                print(f"[AI Filter][DEBUG] Tag [{tname}] matched {len(items)} items:")
                for line in items:
                    print(line)

        return results

    def _extract_json(self, response: str) -> Optional[str]:
        """从 AI 响应中提取 JSON 字符串"""
        if not response or not response.strip():
            return None

        json_str = response.strip()

        if "```json" in json_str:
            parts = json_str.split("```json", 1)
            if len(parts) > 1:
                code_block = parts[1]
                end_idx = code_block.find("```")
                json_str = code_block[:end_idx] if end_idx != -1 else code_block
        elif "```" in json_str:
            parts = json_str.split("```", 2)
            if len(parts) >= 2:
                json_str = parts[1]

        json_str = json_str.strip()
        return json_str if json_str else None

    def _print_formatted_json(self, response: str) -> None:
        """格式化打印 AI 响应中的 JSON，便于 debug 阅读"""
        if not response:
            print("(empty response)")
            return

        json_str = self._extract_json(response)
        if json_str:
            try:
                data = json.loads(json_str)
                if isinstance(data, list):
                    # 数组：每个元素压成一行
                    lines = [json.dumps(item, ensure_ascii=False) for item in data]
                    print("[\n  " + ",\n  ".join(lines) + "\n]")
                else:
                    print(json.dumps(data, ensure_ascii=False, indent=2))
                return
            except json.JSONDecodeError:
                pass

        # JSON 解析失败，直接打印原始响应
        print(response)
