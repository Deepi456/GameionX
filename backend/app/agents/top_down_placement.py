from collections import deque
from math import ceil, floor

from app.models import GameSpec, GoalSpec, LevelSpec, Point

_GRID_STEP = 8
_PLAYER_HALF_WIDTH = 15
_PLAYER_HALF_HEIGHT = 19
_COLLECTIBLE_HALF_SIZE = 12
_GOAL_HALF_SIZE = 21
_CLEARANCE = 2
_MIN_COLLECTIBLE_SPACING = 30

Bounds = tuple[float, float, float, float]
Position = tuple[float, float]


def normalize_top_down_placements(spec: GameSpec) -> GameSpec:
    if spec.game_type != "top_down_collection":
        return spec

    start = (
        spec.player.start.x * spec.world_width,
        spec.player.start.y * spec.world_height,
    )
    updated_levels = []
    for level in spec.levels:
        obstacles = _solid_bounds(spec, level)
        if not _inside_world(
            start,
            _PLAYER_HALF_WIDTH + _CLEARANCE,
            _PLAYER_HALF_HEIGHT + _CLEARANCE,
            spec.world_width,
            spec.world_height,
        ):
            raise ValueError("Top-down player spawn is outside the safe play area.")
        if _overlaps_obstacle(
            start,
            _PLAYER_HALF_WIDTH + _CLEARANCE,
            _PLAYER_HALF_HEIGHT + _CLEARANCE,
            obstacles,
        ):
            raise ValueError("Top-down player spawn overlaps a solid obstacle.")

        reachable = _reachable_player_positions(
            start,
            obstacles,
            spec.world_width,
            spec.world_height,
        )
        if not reachable:
            raise ValueError("Top-down player spawn has no reachable walkable area.")

        placed_collectibles: list[Position] = []
        collectibles = []
        for collectible in level.collectibles:
            requested = (
                collectible.position.x * spec.world_width,
                collectible.position.y * spec.world_height,
            )
            if _collectible_is_reachable(requested, reachable, obstacles):
                position = requested
            else:
                candidates = [
                    candidate
                    for candidate in reachable
                    if _entity_is_clear(
                        candidate,
                        _COLLECTIBLE_HALF_SIZE,
                        _COLLECTIBLE_HALF_SIZE,
                        obstacles,
                        spec.world_width,
                        spec.world_height,
                    )
                    and all(
                        _distance(candidate, previous) >= _MIN_COLLECTIBLE_SPACING
                        for previous in placed_collectibles
                    )
                ]
                if not candidates:
                    raise ValueError(
                        "Top-down collectible has no reachable placement with "
                        "sufficient obstacle clearance."
                    )
                position = min(candidates, key=lambda item: _distance(item, requested))
            placed_collectibles.append(position)
            collectibles.append(
                collectible.model_copy(
                    update={
                        "position": Point(
                            x=position[0] / spec.world_width,
                            y=position[1] / spec.world_height,
                        )
                    }
                )
            )

        goal = level.goal
        if goal is None and spec.win_condition.type == "reach_goal":
            goal = GoalSpec(position=Point(x=0.92, y=0.15), kind="exit")
        if goal is not None:
            requested_goal = (
                goal.position.x * spec.world_width,
                goal.position.y * spec.world_height,
            )
            if _goal_is_reachable(requested_goal, reachable, obstacles):
                goal_position = requested_goal
            else:
                candidates = [
                    candidate
                    for candidate in reachable
                    if _entity_is_clear(
                        candidate,
                        _GOAL_HALF_SIZE,
                        _GOAL_HALF_SIZE,
                        obstacles,
                        spec.world_width,
                        spec.world_height,
                    )
                ]
                if not candidates:
                    raise ValueError(
                        "Top-down goal has no reachable placement with sufficient "
                        "obstacle clearance."
                    )
                goal_position = min(
                    candidates,
                    key=lambda item: _distance(item, requested_goal),
                )
            goal = goal.model_copy(
                update={
                    "position": Point(
                        x=goal_position[0] / spec.world_width,
                        y=goal_position[1] / spec.world_height,
                    )
                }
            )

        updated_levels.append(
            level.model_copy(update={"collectibles": collectibles, "goal": goal})
        )

    normalized = spec.model_copy(update={"levels": updated_levels})
    validate_top_down_placements(normalized)
    return normalized


def validate_top_down_placements(spec: GameSpec) -> None:
    if spec.game_type != "top_down_collection":
        return

    start = (
        spec.player.start.x * spec.world_width,
        spec.player.start.y * spec.world_height,
    )
    for level in spec.levels:
        obstacles = _solid_bounds(spec, level)
        player_half_width = _PLAYER_HALF_WIDTH + _CLEARANCE
        player_half_height = _PLAYER_HALF_HEIGHT + _CLEARANCE
        if not _inside_world(
            start,
            player_half_width,
            player_half_height,
            spec.world_width,
            spec.world_height,
        ) or _overlaps_obstacle(
            start, player_half_width, player_half_height, obstacles
        ):
            raise ValueError("Top-down player spawn overlaps a solid obstacle.")

        reachable = _reachable_player_positions(
            start,
            obstacles,
            spec.world_width,
            spec.world_height,
        )
        for collectible in level.collectibles:
            position = (
                collectible.position.x * spec.world_width,
                collectible.position.y * spec.world_height,
            )
            if not _collectible_is_reachable(position, reachable, obstacles):
                raise ValueError(
                    "Top-down collectible overlaps an obstacle or is unreachable."
                )

        if level.goal is not None:
            goal = (
                level.goal.position.x * spec.world_width,
                level.goal.position.y * spec.world_height,
            )
            if not _goal_is_reachable(goal, reachable, obstacles):
                raise ValueError("Top-down goal overlaps an obstacle or is unreachable.")


def _solid_bounds(spec: GameSpec, level: LevelSpec) -> list[Bounds]:
    bounds = [
        (
            item.position.x * spec.world_width,
            item.position.y * spec.world_height,
            (item.position.x + item.width) * spec.world_width,
            (item.position.y + item.height) * spec.world_height,
        )
        for item in level.scenery
        if item.solid
    ]
    bounds.extend(
        (
            item.position.x * spec.world_width,
            item.position.y * spec.world_height,
            (item.position.x + item.width) * spec.world_width,
            (item.position.y + item.height) * spec.world_height,
        )
        for item in level.walls
    )
    return bounds


def _reachable_player_positions(
    start: Position,
    obstacles: list[Bounds],
    world_width: int,
    world_height: int,
) -> list[Position]:
    half_width = _PLAYER_HALF_WIDTH + _CLEARANCE
    half_height = _PLAYER_HALF_HEIGHT + _CLEARANCE
    min_x_step = ceil((half_width - start[0]) / _GRID_STEP)
    max_x_step = floor((world_width - half_width - start[0]) / _GRID_STEP)
    min_y_step = ceil((half_height - start[1]) / _GRID_STEP)
    max_y_step = floor((world_height - half_height - start[1]) / _GRID_STEP)

    def position(step_x: int, step_y: int) -> Position:
        return (
            start[0] + step_x * _GRID_STEP,
            start[1] + step_y * _GRID_STEP,
        )

    start_steps = (0, 0)
    if _overlaps_obstacle(start, half_width, half_height, obstacles):
        return []

    reachable_steps = {start_steps}
    queue = deque([start_steps])
    positions = [start]
    directions = ((-1, 0), (1, 0), (0, -1), (0, 1))
    while queue:
        step_x, step_y = queue.popleft()
        current = position(step_x, step_y)
        for offset_x, offset_y in directions:
            next_steps = (step_x + offset_x, step_y + offset_y)
            if next_steps in reachable_steps:
                continue
            next_x, next_y = next_steps
            if not (
                min_x_step <= next_x <= max_x_step
                and min_y_step <= next_y <= max_y_step
            ):
                continue
            candidate = position(next_x, next_y)
            if _overlaps_obstacle(candidate, half_width, half_height, obstacles):
                continue
            if not _segment_is_clear(current, candidate, half_width, half_height, obstacles):
                continue
            reachable_steps.add(next_steps)
            queue.append(next_steps)
            positions.append(candidate)
    return positions


def _collectible_is_reachable(
    position: Position,
    reachable: list[Position],
    obstacles: list[Bounds],
) -> bool:
    if _overlaps_obstacle(
        position,
        _COLLECTIBLE_HALF_SIZE + _CLEARANCE,
        _COLLECTIBLE_HALF_SIZE + _CLEARANCE,
        obstacles,
    ):
        return False
    return any(
        abs(player[0] - position[0])
        < _PLAYER_HALF_WIDTH + _COLLECTIBLE_HALF_SIZE
        and abs(player[1] - position[1])
        < _PLAYER_HALF_HEIGHT + _COLLECTIBLE_HALF_SIZE
        for player in reachable
    )


def _goal_is_reachable(
    position: Position,
    reachable: list[Position],
    obstacles: list[Bounds],
) -> bool:
    if _overlaps_obstacle(
        position,
        _GOAL_HALF_SIZE + _CLEARANCE,
        _GOAL_HALF_SIZE + _CLEARANCE,
        obstacles,
    ):
        return False
    return any(
        abs(player[0] - position[0]) < _PLAYER_HALF_WIDTH + _GOAL_HALF_SIZE
        and abs(player[1] - position[1]) < _PLAYER_HALF_HEIGHT + _GOAL_HALF_SIZE
        for player in reachable
    )


def _entity_is_clear(
    position: Position,
    half_width: float,
    half_height: float,
    obstacles: list[Bounds],
    world_width: int,
    world_height: int,
) -> bool:
    return _inside_world(
        position,
        half_width + _CLEARANCE,
        half_height + _CLEARANCE,
        world_width,
        world_height,
    ) and not _overlaps_obstacle(
        position,
        half_width + _CLEARANCE,
        half_height + _CLEARANCE,
        obstacles,
    )


def _inside_world(
    position: Position,
    half_width: float,
    half_height: float,
    world_width: int,
    world_height: int,
) -> bool:
    return (
        half_width <= position[0] <= world_width - half_width
        and half_height <= position[1] <= world_height - half_height
    )


def _overlaps_obstacle(
    position: Position,
    half_width: float,
    half_height: float,
    obstacles: list[Bounds],
) -> bool:
    return any(
        position[0] + half_width > left
        and position[0] - half_width < right
        and position[1] + half_height > top
        and position[1] - half_height < bottom
        for left, top, right, bottom in obstacles
    )


def _segment_is_clear(
    start: Position,
    end: Position,
    half_width: float,
    half_height: float,
    obstacles: list[Bounds],
) -> bool:
    if start[1] == end[1]:
        left, right = sorted((start[0], end[0]))
        return not any(
            start[1] + half_height > top
            and start[1] - half_height < bottom
            and right + half_width > obstacle_left
            and left - half_width < obstacle_right
            for obstacle_left, top, obstacle_right, bottom in obstacles
        )
    top, bottom = sorted((start[1], end[1]))
    return not any(
        start[0] + half_width > obstacle_left
        and start[0] - half_width < obstacle_right
        and bottom + half_height > obstacle_top
        and top - half_height < obstacle_bottom
        for obstacle_left, obstacle_top, obstacle_right, obstacle_bottom in obstacles
    )


def _distance(left: Position, right: Position) -> float:
    return ((left[0] - right[0]) ** 2 + (left[1] - right[1]) ** 2) ** 0.5
