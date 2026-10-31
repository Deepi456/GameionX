from app.models import DefinitionUI


class GameSceneBuilderAgent:
    """Produce safe, declarative HUD and instruction data from runtime capabilities."""

    @staticmethod
    def build_ui(
        *,
        movement_mode: str,
        fire_control: str,
        win_condition: str,
        lose_condition: str,
        has_timer: bool,
        requested_ui: tuple[str, ...] = (),
    ) -> DefinitionUI:
        movement_instructions = {
            "platformer": "Arrow keys / A-D to move · W / ↑ / Space to jump",
            "four_direction": "Arrow keys / WASD to move",
            "flap": "Space / ↑ to flap",
            "racing": "← / → or A / D to steer",
        }[movement_mode]
        instructions = movement_instructions
        start_prompt = "Press a movement key to start"
        if movement_mode == "flap":
            start_prompt = "Press SPACE or ↑ to flap"
        if fire_control != "none":
            fire_key = {"space": "SPACE", "enter": "ENTER", "x": "X"}[fire_control]
            instructions = f"{instructions} · {fire_key} to shoot"
            start_prompt = f"Press {fire_key} to start"

        objective_label = {
            "survive": "Survive",
            "defeat_target": "Defeated",
            "collect_target": "Items",
            "reach_goal": "Goal",
        }[win_condition]
        show_timer = (
            win_condition == "survive"
            or lose_condition == "time_expired"
            or has_timer
            or "timer" in requested_ui
        )
        return DefinitionUI(
            show_score=True,
            show_health=True,
            show_timer=show_timer,
            objective_label=objective_label,
            instructions=instructions,
            start_prompt=start_prompt,
            victory_message="You survived!" if win_condition == "survive" else "You win!",
        )
