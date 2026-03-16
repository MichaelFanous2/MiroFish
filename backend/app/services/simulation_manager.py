"""
OASIS模拟管理器
管理Twitter和Reddit双平台并行模拟
使用预设脚本 + LLM智能生成配置参数
"""

import os
import json
import shutil
from typing import Dict, Any, List, Optional
from dataclasses import dataclass, field
from datetime import datetime
from enum import Enum

from ..config import Config
from ..utils.logger import get_logger
from .zep_entity_reader import ZepEntityReader, FilteredEntities
from .oasis_profile_generator import OasisProfileGenerator, OasisAgentProfile
from .simulation_config_generator import SimulationConfigGenerator, SimulationParameters

logger = get_logger('mirofish.simulation')


class SimulationStatus(str, Enum):
    """模拟状态"""
    CREATED = "created"
    PREPARING = "preparing"
    # Real-people pipeline stages
    CASTING = "casting"
    ENRICHING = "enriching"
    EXTRACTING_OPINIONS = "extracting_opinions"
    BUILDING_PERSONAS = "building_personas"
    READY = "ready"
    RUNNING = "running"
    PAUSED = "paused"
    STOPPED = "stopped"      # 模拟被手动停止
    COMPLETED = "completed"  # 模拟自然完成
    FAILED = "failed"


class PlatformType(str, Enum):
    """平台类型"""
    TWITTER = "twitter"
    REDDIT = "reddit"


@dataclass
class SimulationState:
    """模拟状态"""
    simulation_id: str
    project_id: str
    graph_id: str

    # 平台启用状态
    enable_twitter: bool = True
    enable_reddit: bool = True

    # 状态
    status: SimulationStatus = SimulationStatus.CREATED

    # 准备阶段数据
    entities_count: int = 0
    profiles_count: int = 0
    entity_types: List[str] = field(default_factory=list)

    # 配置生成信息
    config_generated: bool = False
    config_reasoning: str = ""

    # 运行时数据
    current_round: int = 0
    twitter_status: str = "not_started"
    reddit_status: str = "not_started"

    # Real-people mode fields
    use_real_people: bool = False
    groups_generated: bool = False
    groups_approved: bool = False
    enrichment_complete: bool = False

    # 时间戳
    created_at: str = field(default_factory=lambda: datetime.now().isoformat())
    updated_at: str = field(default_factory=lambda: datetime.now().isoformat())

    # 错误信息
    error: Optional[str] = None
    
    def to_dict(self) -> Dict[str, Any]:
        """完整状态字典（内部使用）"""
        return {
            "simulation_id": self.simulation_id,
            "project_id": self.project_id,
            "graph_id": self.graph_id,
            "enable_twitter": self.enable_twitter,
            "enable_reddit": self.enable_reddit,
            "status": self.status.value,
            "entities_count": self.entities_count,
            "profiles_count": self.profiles_count,
            "entity_types": self.entity_types,
            "config_generated": self.config_generated,
            "config_reasoning": self.config_reasoning,
            "current_round": self.current_round,
            "twitter_status": self.twitter_status,
            "reddit_status": self.reddit_status,
            "use_real_people": self.use_real_people,
            "groups_generated": self.groups_generated,
            "groups_approved": self.groups_approved,
            "enrichment_complete": self.enrichment_complete,
            "created_at": self.created_at,
            "updated_at": self.updated_at,
            "error": self.error,
        }
    
    def to_simple_dict(self) -> Dict[str, Any]:
        """简化状态字典（API返回使用）"""
        return {
            "simulation_id": self.simulation_id,
            "project_id": self.project_id,
            "graph_id": self.graph_id,
            "status": self.status.value,
            "entities_count": self.entities_count,
            "profiles_count": self.profiles_count,
            "entity_types": self.entity_types,
            "config_generated": self.config_generated,
            "error": self.error,
        }


class SimulationManager:
    """
    模拟管理器
    
    核心功能：
    1. 从Zep图谱读取实体并过滤
    2. 生成OASIS Agent Profile
    3. 使用LLM智能生成模拟配置参数
    4. 准备预设脚本所需的所有文件
    """
    
    # 模拟数据存储目录
    SIMULATION_DATA_DIR = os.path.join(
        os.path.dirname(__file__), 
        '../../uploads/simulations'
    )
    
    def __init__(self):
        # 确保目录存在
        os.makedirs(self.SIMULATION_DATA_DIR, exist_ok=True)
        
        # 内存中的模拟状态缓存
        self._simulations: Dict[str, SimulationState] = {}
    
    def _get_simulation_dir(self, simulation_id: str) -> str:
        """获取模拟数据目录"""
        sim_dir = os.path.join(self.SIMULATION_DATA_DIR, simulation_id)
        os.makedirs(sim_dir, exist_ok=True)
        return sim_dir
    
    def _save_simulation_state(self, state: SimulationState):
        """保存模拟状态到文件"""
        sim_dir = self._get_simulation_dir(state.simulation_id)
        state_file = os.path.join(sim_dir, "state.json")
        
        state.updated_at = datetime.now().isoformat()
        
        with open(state_file, 'w', encoding='utf-8') as f:
            json.dump(state.to_dict(), f, ensure_ascii=False, indent=2)
        
        self._simulations[state.simulation_id] = state
    
    def _load_simulation_state(self, simulation_id: str) -> Optional[SimulationState]:
        """从文件加载模拟状态"""
        if simulation_id in self._simulations:
            return self._simulations[simulation_id]
        
        sim_dir = self._get_simulation_dir(simulation_id)
        state_file = os.path.join(sim_dir, "state.json")
        
        if not os.path.exists(state_file):
            return None
        
        with open(state_file, 'r', encoding='utf-8') as f:
            data = json.load(f)
        
        # Handle status values added in real-people mode that older state files won't have
        raw_status = data.get("status", "created")
        try:
            parsed_status = SimulationStatus(raw_status)
        except ValueError:
            parsed_status = SimulationStatus.CREATED

        state = SimulationState(
            simulation_id=simulation_id,
            project_id=data.get("project_id", ""),
            graph_id=data.get("graph_id", ""),
            enable_twitter=data.get("enable_twitter", True),
            enable_reddit=data.get("enable_reddit", True),
            status=parsed_status,
            entities_count=data.get("entities_count", 0),
            profiles_count=data.get("profiles_count", 0),
            entity_types=data.get("entity_types", []),
            config_generated=data.get("config_generated", False),
            config_reasoning=data.get("config_reasoning", ""),
            current_round=data.get("current_round", 0),
            twitter_status=data.get("twitter_status", "not_started"),
            reddit_status=data.get("reddit_status", "not_started"),
            use_real_people=data.get("use_real_people", False),
            groups_generated=data.get("groups_generated", False),
            groups_approved=data.get("groups_approved", False),
            enrichment_complete=data.get("enrichment_complete", False),
            created_at=data.get("created_at", datetime.now().isoformat()),
            updated_at=data.get("updated_at", datetime.now().isoformat()),
            error=data.get("error"),
        )
        
        self._simulations[simulation_id] = state
        return state
    
    def create_simulation(
        self,
        project_id: str,
        graph_id: str,
        enable_twitter: bool = True,
        enable_reddit: bool = True,
    ) -> SimulationState:
        """
        创建新的模拟
        
        Args:
            project_id: 项目ID
            graph_id: Zep图谱ID
            enable_twitter: 是否启用Twitter模拟
            enable_reddit: 是否启用Reddit模拟
            
        Returns:
            SimulationState
        """
        import uuid
        simulation_id = f"sim_{uuid.uuid4().hex[:12]}"
        
        state = SimulationState(
            simulation_id=simulation_id,
            project_id=project_id,
            graph_id=graph_id,
            enable_twitter=enable_twitter,
            enable_reddit=enable_reddit,
            status=SimulationStatus.CREATED,
        )
        
        self._save_simulation_state(state)
        logger.info(f"创建模拟: {simulation_id}, project={project_id}, graph={graph_id}")
        
        return state
    
    def prepare_simulation(
        self,
        simulation_id: str,
        simulation_requirement: str,
        document_text: str,
        defined_entity_types: Optional[List[str]] = None,
        use_llm_for_profiles: bool = True,
        progress_callback: Optional[callable] = None,
        parallel_profile_count: int = 3
    ) -> SimulationState:
        """
        准备模拟环境（全程自动化）
        
        步骤：
        1. 从Zep图谱读取并过滤实体
        2. 为每个实体生成OASIS Agent Profile（可选LLM增强，支持并行）
        3. 使用LLM智能生成模拟配置参数（时间、活跃度、发言频率等）
        4. 保存配置文件和Profile文件
        5. 复制预设脚本到模拟目录
        
        Args:
            simulation_id: 模拟ID
            simulation_requirement: 模拟需求描述（用于LLM生成配置）
            document_text: 原始文档内容（用于LLM理解背景）
            defined_entity_types: 预定义的实体类型（可选）
            use_llm_for_profiles: 是否使用LLM生成详细人设
            progress_callback: 进度回调函数 (stage, progress, message)
            parallel_profile_count: 并行生成人设的数量，默认3
            
        Returns:
            SimulationState
        """
        state = self._load_simulation_state(simulation_id)
        if not state:
            raise ValueError(f"模拟不存在: {simulation_id}")
        
        try:
            state.status = SimulationStatus.PREPARING
            self._save_simulation_state(state)
            
            sim_dir = self._get_simulation_dir(simulation_id)
            
            # ========== 阶段1: 读取并过滤实体 ==========
            if progress_callback:
                progress_callback("reading", 0, "正在连接Zep图谱...")
            
            reader = ZepEntityReader()
            
            if progress_callback:
                progress_callback("reading", 30, "正在读取节点数据...")
            
            filtered = reader.filter_defined_entities(
                graph_id=state.graph_id,
                defined_entity_types=defined_entity_types,
                enrich_with_edges=True
            )
            
            state.entities_count = filtered.filtered_count
            state.entity_types = list(filtered.entity_types)
            
            if progress_callback:
                progress_callback(
                    "reading", 100, 
                    f"完成，共 {filtered.filtered_count} 个实体",
                    current=filtered.filtered_count,
                    total=filtered.filtered_count
                )
            
            if filtered.filtered_count == 0:
                state.status = SimulationStatus.FAILED
                state.error = "没有找到符合条件的实体，请检查图谱是否正确构建"
                self._save_simulation_state(state)
                return state
            
            # ========== 阶段2: 生成Agent Profile ==========
            total_entities = len(filtered.entities)
            
            if progress_callback:
                progress_callback(
                    "generating_profiles", 0, 
                    "开始生成...",
                    current=0,
                    total=total_entities
                )
            
            # 传入graph_id以启用Zep检索功能，获取更丰富的上下文
            generator = OasisProfileGenerator(graph_id=state.graph_id)
            
            def profile_progress(current, total, msg):
                if progress_callback:
                    progress_callback(
                        "generating_profiles", 
                        int(current / total * 100), 
                        msg,
                        current=current,
                        total=total,
                        item_name=msg
                    )
            
            # 设置实时保存的文件路径（优先使用 Reddit JSON 格式）
            realtime_output_path = None
            realtime_platform = "reddit"
            if state.enable_reddit:
                realtime_output_path = os.path.join(sim_dir, "reddit_profiles.json")
                realtime_platform = "reddit"
            elif state.enable_twitter:
                realtime_output_path = os.path.join(sim_dir, "twitter_profiles.csv")
                realtime_platform = "twitter"
            
            profiles = generator.generate_profiles_from_entities(
                entities=filtered.entities,
                use_llm=use_llm_for_profiles,
                progress_callback=profile_progress,
                graph_id=state.graph_id,  # 传入graph_id用于Zep检索
                parallel_count=parallel_profile_count,  # 并行生成数量
                realtime_output_path=realtime_output_path,  # 实时保存路径
                output_platform=realtime_platform  # 输出格式
            )
            
            state.profiles_count = len(profiles)
            
            # 保存Profile文件（注意：Twitter使用CSV格式，Reddit使用JSON格式）
            # Reddit 已经在生成过程中实时保存了，这里再保存一次确保完整性
            if progress_callback:
                progress_callback(
                    "generating_profiles", 95, 
                    "保存Profile文件...",
                    current=total_entities,
                    total=total_entities
                )
            
            if state.enable_reddit:
                generator.save_profiles(
                    profiles=profiles,
                    file_path=os.path.join(sim_dir, "reddit_profiles.json"),
                    platform="reddit"
                )
            
            if state.enable_twitter:
                # Twitter使用CSV格式！这是OASIS的要求
                generator.save_profiles(
                    profiles=profiles,
                    file_path=os.path.join(sim_dir, "twitter_profiles.csv"),
                    platform="twitter"
                )
            
            if progress_callback:
                progress_callback(
                    "generating_profiles", 100, 
                    f"完成，共 {len(profiles)} 个Profile",
                    current=len(profiles),
                    total=len(profiles)
                )
            
            # ========== 阶段3: LLM智能生成模拟配置 ==========
            if progress_callback:
                progress_callback(
                    "generating_config", 0, 
                    "正在分析模拟需求...",
                    current=0,
                    total=3
                )
            
            config_generator = SimulationConfigGenerator()
            
            if progress_callback:
                progress_callback(
                    "generating_config", 30, 
                    "正在调用LLM生成配置...",
                    current=1,
                    total=3
                )
            
            sim_params = config_generator.generate_config(
                simulation_id=simulation_id,
                project_id=state.project_id,
                graph_id=state.graph_id,
                simulation_requirement=simulation_requirement,
                document_text=document_text,
                entities=filtered.entities,
                enable_twitter=state.enable_twitter,
                enable_reddit=state.enable_reddit
            )
            
            if progress_callback:
                progress_callback(
                    "generating_config", 70, 
                    "正在保存配置文件...",
                    current=2,
                    total=3
                )
            
            # 保存配置文件
            config_path = os.path.join(sim_dir, "simulation_config.json")
            with open(config_path, 'w', encoding='utf-8') as f:
                f.write(sim_params.to_json())
            
            state.config_generated = True
            state.config_reasoning = sim_params.generation_reasoning
            
            if progress_callback:
                progress_callback(
                    "generating_config", 100, 
                    "配置生成完成",
                    current=3,
                    total=3
                )
            
            # 注意：运行脚本保留在 backend/scripts/ 目录，不再复制到模拟目录
            # 启动模拟时，simulation_runner 会从 scripts/ 目录运行脚本
            
            # 更新状态
            state.status = SimulationStatus.READY
            self._save_simulation_state(state)
            
            logger.info(f"模拟准备完成: {simulation_id}, "
                       f"entities={state.entities_count}, profiles={state.profiles_count}")
            
            return state
            
        except Exception as e:
            logger.error(f"模拟准备失败: {simulation_id}, error={str(e)}")
            import traceback
            logger.error(traceback.format_exc())
            state.status = SimulationStatus.FAILED
            state.error = str(e)
            self._save_simulation_state(state)
            raise
    
    def get_simulation(self, simulation_id: str) -> Optional[SimulationState]:
        """获取模拟状态"""
        return self._load_simulation_state(simulation_id)
    
    def list_simulations(self, project_id: Optional[str] = None) -> List[SimulationState]:
        """列出所有模拟"""
        simulations = []
        
        if os.path.exists(self.SIMULATION_DATA_DIR):
            for sim_id in os.listdir(self.SIMULATION_DATA_DIR):
                # 跳过隐藏文件（如 .DS_Store）和非目录文件
                sim_path = os.path.join(self.SIMULATION_DATA_DIR, sim_id)
                if sim_id.startswith('.') or not os.path.isdir(sim_path):
                    continue
                
                state = self._load_simulation_state(sim_id)
                if state:
                    if project_id is None or state.project_id == project_id:
                        simulations.append(state)
        
        return simulations
    
    def get_profiles(self, simulation_id: str, platform: str = "reddit") -> List[Dict[str, Any]]:
        """获取模拟的Agent Profile"""
        state = self._load_simulation_state(simulation_id)
        if not state:
            raise ValueError(f"模拟不存在: {simulation_id}")
        
        sim_dir = self._get_simulation_dir(simulation_id)
        profile_path = os.path.join(sim_dir, f"{platform}_profiles.json")
        
        if not os.path.exists(profile_path):
            return []
        
        with open(profile_path, 'r', encoding='utf-8') as f:
            return json.load(f)
    
    def get_simulation_config(self, simulation_id: str) -> Optional[Dict[str, Any]]:
        """获取模拟配置"""
        sim_dir = self._get_simulation_dir(simulation_id)
        config_path = os.path.join(sim_dir, "simulation_config.json")
        
        if not os.path.exists(config_path):
            return None
        
        with open(config_path, 'r', encoding='utf-8') as f:
            return json.load(f)
    
    def get_run_instructions(self, simulation_id: str) -> Dict[str, str]:
        """获取运行说明"""
        sim_dir = self._get_simulation_dir(simulation_id)
        config_path = os.path.join(sim_dir, "simulation_config.json")
        scripts_dir = os.path.abspath(os.path.join(os.path.dirname(__file__), '../../scripts'))
        
        return {
            "simulation_dir": sim_dir,
            "scripts_dir": scripts_dir,
            "config_file": config_path,
            "commands": {
                "twitter": f"python {scripts_dir}/run_twitter_simulation.py --config {config_path}",
                "reddit": f"python {scripts_dir}/run_reddit_simulation.py --config {config_path}",
                "parallel": f"python {scripts_dir}/run_parallel_simulation.py --config {config_path}",
            },
            "instructions": (
                f"1. 激活conda环境: conda activate MiroFish\n"
                f"2. 运行模拟 (脚本位于 {scripts_dir}):\n"
                f"   - 单独运行Twitter: python {scripts_dir}/run_twitter_simulation.py --config {config_path}\n"
                f"   - 单独运行Reddit: python {scripts_dir}/run_reddit_simulation.py --config {config_path}\n"
                f"   - 并行运行双平台: python {scripts_dir}/run_parallel_simulation.py --config {config_path}"
            )
        }

    # =========================================================================
    # Real-people simulation pipeline
    # =========================================================================

    def prepare_simulation_real_people(
        self,
        simulation_id: str,
        simulation_requirement: str,
        document_text: str,
        progress_callback: Optional[callable] = None,
    ) -> "SimulationState":
        """
        Prepare a simulation using real Nyne-enriched people.

        Requires that groups have already been generated and approved
        (state.groups_approved == True and cast_groups.json exists on disk).

        Pipeline:
          1. Load approved groups from disk
          2. Enrich all members with Nyne (ENRICHING)
          3. Extract opinions per person (EXTRACTING_OPINIONS)
          4. Build real OasisAgentProfiles (BUILDING_PERSONAS)
          5. Generate simulation config (same as synthetic path)
          6. Save all files, mark READY
        """
        from .nyne.nyne_client import NyneClient
        from .nyne.enrichment_pipeline import EnrichmentPipeline, load_progress
        from .nyne.opinion_extractor import OpinionExtractor
        from .nyne.cast_assembler import (
            load_groups, save_groups, CastAssembler,
        )
        from .persona.real_persona_builder import RealPersonaBuilder
        from .oasis_profile_generator import OasisProfileGenerator
        from ..utils.llm_client import LLMClient

        state = self._load_simulation_state(simulation_id)
        if not state:
            raise ValueError(f"模拟不存在: {simulation_id}")
        if not state.groups_approved:
            raise ValueError("Groups not yet approved — call /groups/approve first")

        sim_dir = self._get_simulation_dir(simulation_id)

        try:
            llm = LLMClient()
            nyne = NyneClient()

            # ── Stage 1: Load approved groups ──────────────────────────────
            groups = load_groups(sim_dir)
            if not groups:
                raise ValueError("No groups found on disk — generate and approve groups first")

            from .nyne.cast_assembler import CastAssembler
            all_members = CastAssembler.all_members(groups)
            real_members = CastAssembler.real_members(groups)
            synthetic_members = CastAssembler.synthetic_members(groups)

            logger.info(
                f"Cast: {len(all_members)} total, "
                f"{len(real_members)} real, {len(synthetic_members)} synthetic fallback"
            )

            if not all_members:
                raise ValueError("Cast is empty — add people to at least one group before approving")

            if not real_members:
                logger.warning(
                    "No real members in cast — all slots are synthetic fallback. "
                    "Skipping Nyne enrichment and running synthetic-only pipeline."
                )

            # ── Stage 2: Enrich real members (skip if all synthetic) ───────
            state.status = SimulationStatus.ENRICHING
            self._save_simulation_state(state)
            if progress_callback:
                msg = (f"Enriching {len(real_members)} people via Nyne..."
                       if real_members else "All slots synthetic — skipping Nyne enrichment")
                progress_callback("enriching", 0, msg)

            pipeline = EnrichmentPipeline(nyne, sim_dir)

            enrichment_results = pipeline.run(
                members=real_members,  # empty list is handled gracefully by pipeline
                progress_callback=lambda done, total, name, status: (
                    progress_callback("enriching", int(done / max(total, 1) * 100), name)
                    if progress_callback else None
                ),
                max_concurrent=Config.NYNE_MAX_CONCURRENT,
            )

            # ── Stage 3: Extract opinions ──────────────────────────────────
            state.status = SimulationStatus.EXTRACTING_OPINIONS
            self._save_simulation_state(state)
            if progress_callback:
                progress_callback("extracting_opinions", 0, "Extracting opinions from real posts...")

            extractor = OpinionExtractor(llm)
            enriched_people = [p for p in enrichment_results.values() if p is not None]

            opinion_map = extractor.extract_batch(
                people=enriched_people,
                topic=simulation_requirement,
                progress_callback=lambda done, total, name: (
                    progress_callback("extracting_opinions", int(done / max(total, 1) * 100), name)
                    if progress_callback else None
                ),
            )

            # ── Stage 4: Build personas ────────────────────────────────────
            state.status = SimulationStatus.BUILDING_PERSONAS
            self._save_simulation_state(state)
            if progress_callback:
                progress_callback("building_personas", 0, "Building persona profiles...")

            builder = RealPersonaBuilder(llm)
            # Synthetic fallback — use existing LLM-only generator
            synthetic_generator = OasisProfileGenerator(graph_id=state.graph_id)

            profiles = []
            user_id_counter = 1

            # Build member_id → CastMember lookup for entity_uuid access
            member_lookup = {m.member_id: m for m in all_members}

            for member in all_members:
                person_data = enrichment_results.get(member.member_id)
                if person_data is not None:
                    opinion = opinion_map.get(person_data.linkedin_url)
                    if opinion is None:
                        from .nyne.opinion_extractor import PersonOpinionProfile
                        opinion = PersonOpinionProfile(
                            person_name=person_data.name,
                            linkedin_url=person_data.linkedin_url,
                            topic=simulation_requirement,
                        )
                    profile = builder.build(
                        person=person_data,
                        opinion=opinion,
                        user_id=user_id_counter,
                        topic=simulation_requirement,
                        source_entity_uuid=member.entity_uuid,
                        source_entity_type="real_person",
                    )
                    profiles.append(profile)
                    user_id_counter += 1

            # Generate synthetic profiles for fallback members
            # Create minimal EntityNode-like objects for the synthetic generator
            if synthetic_members:
                from .zep_entity_reader import EntityNode
                synthetic_entities = []
                for m in synthetic_members:
                    node = EntityNode(
                        uuid=m.member_id,
                        name=m.name,
                        labels=[m.role or "Person"],
                        summary=f"Synthetic agent representing: {m.role}",
                        attributes={},
                    )
                    synthetic_entities.append(node)

                synthetic_profiles = synthetic_generator.generate_profiles_from_entities(
                    entities=synthetic_entities,
                    use_llm=True,
                    start_user_id=user_id_counter,
                )
                profiles.extend(synthetic_profiles)

            state.profiles_count = len(profiles)

            if progress_callback:
                progress_callback("building_personas", 100, f"Built {len(profiles)} profiles")

            # ── Stage 5: Save profile files ────────────────────────────────
            from .oasis_profile_generator import OasisProfileGenerator as Gen
            gen_instance = OasisProfileGenerator(graph_id=state.graph_id)

            if state.enable_reddit:
                gen_instance.save_profiles(
                    profiles=profiles,
                    file_path=os.path.join(sim_dir, "reddit_profiles.json"),
                    platform="reddit",
                )
            if state.enable_twitter:
                gen_instance.save_profiles(
                    profiles=profiles,
                    file_path=os.path.join(sim_dir, "twitter_profiles.csv"),
                    platform="twitter",
                )

            # ── Stage 6: Generate simulation config (same as synthetic) ────
            if progress_callback:
                progress_callback("generating_config", 0, "Generating simulation config...")

            config_generator = SimulationConfigGenerator()

            # Build a minimal FilteredEntities-like structure from real members
            # so the config generator can use real stance/activity data
            from .zep_entity_reader import EntityNode, FilteredEntities
            pseudo_entities = []
            for profile in profiles:
                node = EntityNode(
                    uuid=getattr(profile, "source_entity_uuid", None) or profile.user_name,
                    name=profile.name,
                    labels=[profile.profession or "Person"],
                    summary=profile.bio,
                    attributes={},
                )
                pseudo_entities.append(node)

            sim_params = config_generator.generate_config(
                simulation_id=simulation_id,
                project_id=state.project_id,
                graph_id=state.graph_id,
                simulation_requirement=simulation_requirement,
                document_text=document_text,
                entities=pseudo_entities,
                enable_twitter=state.enable_twitter,
                enable_reddit=state.enable_reddit,
            )

            # Patch in real activity/stance data where available
            for agent_cfg in sim_params.agent_configs:
                matched_profile = next(
                    (p for p in profiles if p.name == agent_cfg.entity_name), None
                )
                if matched_profile:
                    if hasattr(matched_profile, "_activity_level"):
                        agent_cfg.activity_level = matched_profile._activity_level
                    if hasattr(matched_profile, "_sentiment_bias"):
                        agent_cfg.sentiment_bias = matched_profile._sentiment_bias
                    if hasattr(matched_profile, "_stance"):
                        agent_cfg.stance = matched_profile._stance
                    if hasattr(matched_profile, "_active_hours"):
                        agent_cfg.active_hours = matched_profile._active_hours
                    if hasattr(matched_profile, "_influence_weight"):
                        agent_cfg.influence_weight = matched_profile._influence_weight

            config_path = os.path.join(sim_dir, "simulation_config.json")
            with open(config_path, "w", encoding="utf-8") as f:
                f.write(sim_params.to_json())

            state.config_generated = True
            state.config_reasoning = sim_params.generation_reasoning
            state.enrichment_complete = True

            # ── Save grounding report ───────────────────────────────────────
            self._save_grounding_report(
                sim_dir=sim_dir,
                groups=groups,
                enrichment_results=enrichment_results,
                opinion_map=opinion_map,
                synthetic_count=len(synthetic_members),
            )

            state.status = SimulationStatus.READY
            self._save_simulation_state(state)

            logger.info(
                f"Real-people simulation ready: {simulation_id}, "
                f"profiles={state.profiles_count} "
                f"({len(real_members)} real, {len(synthetic_members)} synthetic)"
            )
            return state

        except Exception as e:
            logger.error(f"Real-people simulation prep failed: {simulation_id} — {e}")
            import traceback
            logger.error(traceback.format_exc())
            state.status = SimulationStatus.FAILED
            state.error = str(e)
            self._save_simulation_state(state)
            raise

    def _save_grounding_report(
        self,
        sim_dir: str,
        groups,
        enrichment_results: dict,
        opinion_map: dict,
        synthetic_count: int,
    ):
        """Write grounding_report.json so the report agent can cite real evidence."""
        from .nyne.cast_assembler import CastAssembler

        total_real = sum(1 for v in enrichment_results.values() if v is not None)
        total = total_real + synthetic_count
        overall_grounding = (
            sum(
                op.confidence
                for op in opinion_map.values()
            ) / max(len(opinion_map), 1)
        ) if opinion_map else 0.0

        report = {
            "mode": "real_people",
            "overall_grounding": round(overall_grounding, 3),
            "total_agents": total,
            "real_agents": total_real,
            "synthetic_fallback_count": synthetic_count,
            "groups": [],
        }

        # Build per-group entries
        member_id_to_result = {}
        for group in groups:
            for m in group.members:
                member_id_to_result[m.member_id] = m

        for group in groups:
            group_entry = {
                "name": group.name,
                "members": [],
            }
            for member in group.members:
                person = enrichment_results.get(member.member_id)
                if person:
                    opinion = opinion_map.get(person.linkedin_url)
                    citations = [
                        p.get("url") for p in (opinion.relevant_posts if opinion else [])
                        if p.get("url")
                    ]
                    group_entry["members"].append({
                        "name": person.name,
                        "linkedin_url": person.linkedin_url,
                        "grounding_level": opinion.grounding_level if opinion else "inferred",
                        "real_posts_found": len(opinion.relevant_posts) if opinion else 0,
                        "stance": opinion.stance if opinion else "neutral",
                        "stance_confidence": round(opinion.confidence, 3) if opinion else 0.0,
                        "citations": citations[:3],
                    })
                else:
                    group_entry["members"].append({
                        "name": member.name,
                        "source": member.source,
                        "grounding_level": "synthetic",
                    })
            report["groups"].append(group_entry)

        report_path = os.path.join(sim_dir, "grounding_report.json")
        with open(report_path, "w", encoding="utf-8") as f:
            json.dump(report, f, ensure_ascii=False, indent=2)

        logger.info(f"Grounding report saved: overall={overall_grounding:.2f}, path={report_path}")
