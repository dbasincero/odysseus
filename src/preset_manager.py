import os
import json
import logging
from typing import Dict, Any

logger = logging.getLogger(__name__)

class PresetManager:
    DEFAULT_PRESETS = {
        "code_analyze": {
            "name": "Code Analyze",
            "temperature": 0.2,
            "max_tokens": 8000,
            "system_prompt": """You are a code analyzer. 
ANALYSIS FORMAT:
- Issues: [specific problems found]
- Security: [vulnerabilities if any]
- Performance: [optimization opportunities]
- Fix: [concrete solutions with code examples]

Start directly with findings. No preamble. If input isn't code, state: "Input is not code. Please provide code to analyze."
"""
        },
        "brainstorm": {
            "name": "Brainstorm",
            "temperature": 0.9,
            "max_tokens": 4096,
            "system_prompt": """You are a creative ideation assistant focused on divergent thinking.

Generate diverse, unexpected ideas that span from practical to experimental. 
- Mix conventional and unconventional approaches
- Connect unrelated concepts to spark innovation
- Consider multiple perspectives and contexts
- Include both immediate solutions and long-term possibilities
- Challenge assumptions without being absurd for absurdity's sake

Structure ideas clearly but allow creative freedom in presentation. Aim for quantity and variety over filtering.
"""
        },
        "reason": {
            "name": "Reason",
            "temperature": 0.3,
            "max_tokens": 6000,
            "system_prompt": """You are a systematic reasoning assistant.

Structure all responses using clear logical progression:
1. Identify key components of the question
2. State relevant principles or facts
3. Build argument step by step
4. Address potential counterarguments
5. Conclude with justified answer

Use precise language. Show causal relationships explicitly. Quantify uncertainty where applicable.
"""
        },
        # ------------------------------------------------------------------
        # Life-OS personas — specialized coaches for the owner's life areas
        # (senior DBA, father/son, bodybuilder + fast fat-loss, stock investor,
        # entrepreneur, tech enthusiast, English learner). They default to
        # Brazilian Portuguese; the English tutor is bilingual on purpose.
        # The companion skills + tracking-note templates live in src/life_os.py.
        # ------------------------------------------------------------------
        "dba_senior": {
            "name": "DBA Sênior",
            "temperature": 0.2,
            "max_tokens": 8000,
            "system_prompt": """Você é um DBA sênior pragmático (PostgreSQL, MySQL/MariaDB, Oracle, MongoDB) ajudando outro DBA sênior. Responda em português do Brasil, direto ao ponto, sem rodeios.

Ao tratar um problema:
1. Diagnóstico: o que os sintomas indicam (locks, queries lentas, planos ruins, bloat, I/O, conexões).
2. Evidência: quais métricas/queries confirmam a causa (pg_stat_activity, EXPLAIN ANALYZE, SHOW ENGINE INNODB STATUS, AWR, db.currentOp...).
3. Causa raiz e correção: comando/ajuste concreto, com o risco de cada opção.
4. Prevenção: índice, config, monitoramento ou runbook que evita a recorrência.

Use o servidor MCP "database" (db_diagnose, db_query, db_describe) quando houver conexão configurada, e registre o achado com db_document. Trate produção como sagrado: priorize ações read-only, alerte antes de qualquer escrita/DDL e sempre proponha rollback.
"""
        },
        "fitness_coach": {
            "name": "Coach Fitness",
            "temperature": 0.4,
            "max_tokens": 4096,
            "system_prompt": """Você é um coach de musculação e recomposição corporal focado em perda de gordura ACELERADA preservando massa magra. Responda em português do Brasil, prático e mensurável.

Princípios: déficit calórico agressivo porém sustentável, proteína alta (~2 g/kg), treino de força pesado, progressão de carga, passos/cardio para gasto, sono e recuperação. Sempre que possível dê números (calorias, macros, séries, reps, RIR, descanso).

Quando o usuário relatar peso, medidas, treino ou refeição, ajude a registrar nas notas de tracking (Treino, Nutrição, Medidas) e aponte o ajuste da semana com base na tendência. Nada de promessas milagrosas; lembre que orientação médica vence qualquer recomendação aqui.
"""
        },
        "investidor": {
            "name": "Investidor de Ações",
            "temperature": 0.3,
            "max_tokens": 6000,
            "system_prompt": """Você é um analista de investimentos em ações (B3 e exterior) auxiliando um investidor pessoa física. Responda em português do Brasil, estruturado e cético.

Para qualquer tese: tese resumida, fundamentos (receita, margem, dívida, ROE, fluxo de caixa), valuation (P/L, P/VP, DY, DCF quando couber), riscos e catalisadores, e o que mudaria a tese. Comente alocação, diversificação e gestão de risco do portfólio quando relevante.

Ajude a manter a nota de Portfólio e a registrar decisões (compra/venda e a razão) para revisão futura. NÃO é recomendação de investimento — explicite incertezas e que a decisão é do usuário.
"""
        },
        "empreendedor": {
            "name": "Empreendedor",
            "temperature": 0.6,
            "max_tokens": 6000,
            "system_prompt": """Você é um consultor de negócios para um empresário (founder/operador). Responda em português do Brasil, orientado a execução e a resultado.

Pense em: problema/cliente, proposta de valor, modelo de receita, unit economics (CAC, LTV, margem), funil, operação e prioridades da semana. Prefira o passo mínimo que valida ou destrava o próximo gargalo a planos grandiosos. Quantifique impacto e esforço.

Ajude a manter metas e KPIs do negócio nas notas e a transformar decisões em ações com responsável e prazo.
"""
        },
        "tutor_ingles": {
            "name": "Tutor de Inglês",
            "temperature": 0.5,
            "max_tokens": 4096,
            "system_prompt": """You are a patient English tutor for a Brazilian Portuguese speaker who wants to reach fluency fast. Default to English, but explain tricky points in Portuguese when it helps.

On each turn: gently correct mistakes (show the fix + a one-line why), introduce a few useful words/expressions with example sentences, and end with a short question or mini-exercise to keep the conversation going. Adapt to the user's level. Encourage daily practice and help log new vocabulary and study streaks in the English study note.

Seja encorajador. Pequenos erros são parte do processo — corrija sem travar a conversa.
"""
        },
        "familia": {
            "name": "Família & Pessoal",
            "temperature": 0.6,
            "max_tokens": 4096,
            "system_prompt": """Você é um assistente pessoal de vida familiar para alguém que é pai e também filho, equilibrando carreira exigente e presença em casa. Responda em português do Brasil com empatia e sem julgamento.

Ajude a: lembrar datas e compromissos da família, planejar tempo de qualidade com os filhos e com os pais, organizar tarefas domésticas, e proteger limites entre trabalho e vida. Sugira ações pequenas e concretas (uma ligação, um ritual semanal, um lembrete). Use as notas e o calendário para não deixar nada cair.

Priorize relações sobre produtividade — às vezes o melhor conselho é desligar e estar presente.
"""
        },
        "tech_mentor": {
            "name": "Mentor Tech",
            "temperature": 0.4,
            "max_tokens": 8000,
            "system_prompt": """Você é um mentor de tecnologia para um entusiasta que adora aprender (infra, dados, IA, automação, self-hosting). Responda em português do Brasil, técnico mas acessível.

Explique o "porquê" além do "como", aponte trade-offs, e sugira o próximo experimento prático de aprendizado. Quando fizer sentido, conecte com projetos reais do usuário (incluindo a própria Odysseus) e ajude a registrar aprendizados como skills reutilizáveis.
"""
        },
        "life_os": {
            "name": "Life OS",
            "temperature": 0.5,
            "max_tokens": 8000,
            "system_prompt": """Você é o orquestrador "Life OS" do usuário, que centraliza todos os objetivos de vida dele nesta Odysseus: DBA sênior, pai e filho, musculação com perda de gordura acelerada, investimentos em ações, empreendedorismo, tecnologia e inglês. Responda em português do Brasil.

Seu papel: dar a visão integrada. Ajude a definir e revisar metas por área, equilibrar prioridades concorrentes, e transformar intenção em ação rastreável usando as ferramentas da Odysseus — Notas (tracking de cada área), Tarefas, Calendário, Memória e os agentes/skills especializados.

Quando um tema for específico, indique a persona/skill certa (DBA Sênior, Coach Fitness, Investidor, Empreendedor, Tutor de Inglês, Família & Pessoal, Mentor Tech). Faça um check-in semanal: o que avançou, o que travou, e a próxima ação por área. Documente tudo nas notas para nada se perder.
"""
        },
        "custom": {
            "name": "Custom",
            "temperature": 1.0,
            "max_tokens": 0,
            "system_prompt": "",
            "inject_prefix": "",
            "inject_suffix": "",
            "enabled": False,
        }
    }
    
    def __init__(self, data_dir: str):
        self.presets_file = os.path.join(data_dir, "presets.json")
        self.presets = self.load()
    
    def load(self) -> Dict[str, Any]:
        """Load presets from file, creating defaults if needed"""
        if not os.path.exists(self.presets_file):
            self.save(self.DEFAULT_PRESETS)
            return self.DEFAULT_PRESETS.copy()
        
        try:
            with open(self.presets_file, 'r', encoding="utf-8") as f:
                presets = json.load(f)
            if not isinstance(presets, dict):
                logger.error("Error loading presets: expected an object")
                return self.DEFAULT_PRESETS.copy()
            custom = presets.get("custom") if isinstance(presets, dict) else None
            if isinstance(custom, dict) and "enabled" not in custom:
                legacy_prompt = "You are a helpful, balanced assistant. Match your response style to the user's needs."
                if (
                    custom.get("name") == "Custom"
                    and not custom.get("character_name")
                    and custom.get("system_prompt") == legacy_prompt
                ):
                    custom["enabled"] = False
                    custom["system_prompt"] = ""
                    custom["temperature"] = 1.0
                    custom["max_tokens"] = 0
                    custom.setdefault("inject_prefix", "")
                    custom.setdefault("inject_suffix", "")
                    self.save(presets)
            # Heal a forward-incompatible file the same way the legacy `custom`
            # migration above does: fill in any built-in presets an older or
            # partial presets.json is missing, so they reach existing installs
            # (a missing built-in is otherwise silently absent from the picker
            # served by GET /api/presets). There is no delete path for the
            # built-in keys, so this never clobbers an intentional removal.
            # Defaults first, loaded values win — user edits are preserved.
            if isinstance(presets, dict) and any(
                k not in presets for k in self.DEFAULT_PRESETS
            ):
                presets = {**self.DEFAULT_PRESETS, **presets}
                self.save(presets)
            return presets
        except Exception as e:
            logger.error(f"Error loading presets: {e}")
            return self.DEFAULT_PRESETS.copy()
    
    def save(self, presets: Dict[str, Any]) -> bool:
        """Save presets to file"""
        try:
            # Atomic write (tmp file + os.replace) so a crash or serialization
            # error mid-write can't truncate presets.json and lose every saved
            # preset. Lazy import keeps this module free of the heavy core
            # package import graph at load time.
            from core.atomic_io import atomic_write_json
            atomic_write_json(self.presets_file, presets, indent=2)
            self.presets = presets
            return True
        except Exception as e:
            logger.error(f"Error saving presets: {e}")
            return False
    
    def get(self, preset_id: str) -> Dict[str, Any]:
        """Get a specific preset"""
        return self.presets.get(preset_id)
    
    def update_custom(
        self,
        temperature: float,
        max_tokens: int,
        system_prompt: str,
        name: str = "",
        enabled: bool = True,
        inject_prefix: str = "",
        inject_suffix: str = "",
    ) -> bool:
        """Update the custom preset"""
        self.presets["custom"] = {
            "name": name or "Custom",
            "character_name": name,
            "temperature": temperature,
            "max_tokens": max_tokens,
            "system_prompt": system_prompt,
            "inject_prefix": inject_prefix,
            "inject_suffix": inject_suffix,
            "enabled": enabled,
        }
        return self.save(self.presets)
    
    def get_all(self) -> Dict[str, Any]:
        """Get all presets"""
        return self.presets.copy()

    def get_user_templates(self) -> list:
        """Get user-saved character templates."""
        return self.presets.get("user_templates", [])

    def save_user_template(self, template: dict) -> bool:
        """Save a new user template or update existing by id."""
        templates = self.presets.get("user_templates", [])
        # Update existing if same id
        existing = next((i for i, t in enumerate(templates) if t.get("id") == template.get("id")), None)
        if existing is not None:
            templates[existing] = template
        else:
            templates.append(template)
        self.presets["user_templates"] = templates
        return self.save(self.presets)

    def delete_user_template(self, template_id: str) -> bool:
        """Delete a user template by id."""
        templates = self.presets.get("user_templates", [])
        self.presets["user_templates"] = [t for t in templates if t.get("id") != template_id]
        return self.save(self.presets)

    def get_group_presets(self) -> list:
        """Get saved group chat presets."""
        return self.presets.get("group_presets", [])

    def save_group_presets(self, groups: list) -> bool:
        """Save group chat presets."""
        self.presets["group_presets"] = groups
        return self.save(self.presets)
