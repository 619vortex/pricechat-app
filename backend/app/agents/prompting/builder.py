from dataclasses import dataclass

from app.agents.prompting.context import PromptContext
from app.agents.prompting.sections import DEFAULT_SECTIONS, PromptSection, SectionRenderer


@dataclass(frozen=True, slots=True)
class CompiledPrompt:
    text: str
    sections: list[PromptSection]

    @property
    def section_keys(self) -> list[str]:
        return [s.key for s in self.sections]


class SystemPromptBuilder:
    """Assembles the system prompt from independent sections, in order."""

    def __init__(self, sections: tuple[SectionRenderer, ...] = DEFAULT_SECTIONS) -> None:
        self.sections = sections

    def build(self, ctx: PromptContext) -> CompiledPrompt:
        rendered = [s for s in (render(ctx) for render in self.sections) if s and s.body.strip()]
        blocks = [s.body if s.title is None else f"## {s.title}\n{s.body}" for s in rendered]
        return CompiledPrompt(text="\n\n".join(blocks), sections=rendered)
