"""Tools for the planner agent to help with task planning and analysis."""

import json
from langchain_core.tools import tool
from typing import Dict, List, Any


@tool
async def analyze_task_complexity(task_description: str) -> str:
    """分析任务的复杂度和所需资源。
    
    参数:
        task_description: 任务描述
    
    返回:
        JSON 格式的分析结果，包含复杂度评分和资源需求
    """
    # 这里可以集成更复杂的分析逻辑
    # 目前使用简单的启发式规则
    
    complexity_score = 0.5  # 0-1 的复杂度评分
    if len(task_description) > 100:
        complexity_score = 0.7
    if "train" in task_description.lower() or "model" in task_description.lower():
        complexity_score = 0.8
    if "multi" in task_description.lower() or "complex" in task_description.lower():
        complexity_score = 0.9
    
    analysis = {
        "complexity_score": complexity_score,
        "estimated_steps": max(3, int(len(task_description) / 50)),
        "resources_needed": ["huggingface_search", "code_generation", "report_generation"],
        "risk_assessment": "medium" if complexity_score < 0.8 else "high",
        "recommended_approach": "sequential" if complexity_score < 0.7 else "phased"
    }
    
    return json.dumps(analysis, indent=2, ensure_ascii=False)


@tool
async def decompose_task(task_description: str) -> str:
    """将复杂任务分解为可执行的子任务。
    
    参数:
        task_description: 任务描述
    
    返回:
        JSON 格式的任务分解结果
    """
    # 根据任务类型进行分解
    task_lower = task_description.lower()
    
    if any(keyword in task_lower for keyword in ["train", "model", "fine-tune"]):
        # 模型训练任务
        subtasks = [
            {"id": 1, "name": "搜索合适的模型", "tool": "search_huggingface_hub", "params": {"entity_type": "model"}},
            {"id": 2, "name": "搜索训练数据集", "tool": "search_huggingface_hub", "params": {"entity_type": "dataset"}},
            {"id": 3, "name": "生成训练代码", "tool": "propose_training_code", "params": {}},
            {"id": 4, "name": "生成最终报告", "tool": "save_final_report", "params": {}}
        ]
    elif any(keyword in task_lower for keyword in ["search", "find", "look for"]):
        # 搜索任务
        subtasks = [
            {"id": 1, "name": "搜索相关资源", "tool": "search_huggingface_hub", "params": {"entity_type": "dataset"}},
            {"id": 2, "name": "生成搜索结果报告", "tool": "save_final_report", "params": {}}
        ]
    else:
        # 通用任务
        subtasks = [
            {"id": 1, "name": "分析任务需求", "tool": "analyze_task_complexity", "params": {}},
            {"id": 2, "name": "搜索相关资源", "tool": "search_huggingface_hub", "params": {"entity_type": "model"}},
            {"id": 3, "name": "生成执行计划", "tool": None, "params": {}}
        ]
    
    decomposition = {
        "original_task": task_description,
        "subtasks": subtasks,
        "total_steps": len(subtasks),
        "execution_order": "sequential"
    }
    
    return json.dumps(decomposition, indent=2, ensure_ascii=False)


@tool
async def validate_plan(plan_json: str) -> str:
    """验证执行计划的完整性和可行性。
    
    参数:
        plan_json: JSON 格式的执行计划
    
    返回:
        验证结果和修改建议
    """
    try:
        plan = json.loads(plan_json)
        
        issues = []
        suggestions = []
        
        # 检查必需字段
        required_fields = ["steps", "resources", "expected_output"]
        for field in required_fields:
            if field not in plan:
                issues.append(f"缺少必需字段: {field}")
                suggestions.append(f"添加 {field} 字段")
        
        # 检查步骤格式
        if "steps" in plan:
            if not isinstance(plan["steps"], list):
                issues.append("steps 必须是数组")
                suggestions.append("将 steps 改为数组格式")
            elif len(plan["steps"]) == 0:
                issues.append("执行步骤为空")
                suggestions.append("添加具体的执行步骤")
            else:
                # 检查每个步骤的格式
                for i, step in enumerate(plan["steps"]):
                    if not isinstance(step, dict):
                        issues.append(f"步骤 {i+1} 不是对象")
                        suggestions.append(f"步骤 {i+1} 应改为对象格式")
                    elif "description" not in step:
                        issues.append(f"步骤 {i+1} 缺少描述")
                        suggestions.append(f"为步骤 {i+1} 添加 description 字段")
        
        # 生成验证报告
        validation_result = {
            "is_valid": len(issues) == 0,
            "issues": issues,
            "suggestions": suggestions,
            "severity": "high" if len(issues) > 3 else "medium" if len(issues) > 0 else "low"
        }
        
        return json.dumps(validation_result, indent=2, ensure_ascii=False)
    
    except json.JSONDecodeError:
        return json.dumps({
            "is_valid": False,
            "issues": ["JSON 格式错误"],
            "suggestions": ["检查 JSON 格式是否正确"],
            "severity": "high"
        }, indent=2, ensure_ascii=False)


@tool
async def generate_plan_template(task_type: str = "general") -> str:
    """根据任务类型生成计划模板。
    
    参数:
        task_type: 任务类型 (training, search, evaluation, general)
    
    返回:
        JSON 格式的计划模板
    """
    templates = {
        "training": {
            "goal": "模型训练任务",
            "steps": [
                {"step": 1, "action": "搜索预训练模型", "tool": "search_huggingface_hub", "params": {"entity_type": "model"}},
                {"step": 2, "action": "搜索训练数据集", "tool": "search_huggingface_hub", "params": {"entity_type": "dataset"}},
                {"step": 3, "action": "生成训练代码", "tool": "propose_training_code", "params": {}},
                {"step": 4, "action": "生成训练报告", "tool": "save_final_report", "params": {}}
            ],
            "resources": ["模型仓库", "数据集", "计算资源"],
            "expected_output": "训练完成的模型和评估报告"
        },
        "search": {
            "goal": "资源搜索任务",
            "steps": [
                {"step": 1, "action": "搜索相关资源", "tool": "search_huggingface_hub", "params": {"entity_type": "dataset"}},
                {"step": 2, "action": "生成搜索结果报告", "tool": "save_final_report", "params": {}}
            ],
            "resources": ["搜索关键词", "筛选条件"],
            "expected_output": "搜索结果摘要报告"
        },
        "general": {
            "goal": "通用任务",
            "steps": [
                {"step": 1, "action": "分析任务需求", "tool": "analyze_task_complexity", "params": {}},
                {"step": 2, "action": "制定详细计划", "tool": None, "params": {}}
            ],
            "resources": ["任务描述", "可用工具"],
            "expected_output": "可执行的详细计划"
        }
    }
    
    template = templates.get(task_type, templates["general"])
    return json.dumps(template, indent=2, ensure_ascii=False)


def get_planner_tools():
    """获取所有规划器工具"""
    return [analyze_task_complexity, decompose_task, validate_plan, generate_plan_template]