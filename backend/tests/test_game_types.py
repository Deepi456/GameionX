from app.provider import DevelopmentMockProvider
from app.agents.game_builder import GameBuilderAgent
from app.agents.game_designer import GameDesignerAgent, PromptConsistencyError
from app.agents.game_qa import GameQAAgent
from app.models import GameSpec, GoalSpec, WinCondition


def test_development_provider_selects_distinct_game_types() -> None:
    provider = DevelopmentMockProvider()

    assert provider.generate("A top-down collection adventure", "beginner").game_type == "top_down_collection"
    assert provider.generate("A neon maze exploration game", "beginner").game_type == "maze"
    dodge = provider.generate("A dodge arena where I survive", "beginner")
    assert dodge.game_type == "dodge_arena"
    assert dodge.win_condition.type == "survive"
    assert provider.generate("A target arcade challenge", "beginner").win_condition.type == "collect_target"
    assert provider.generate("A platform adventure", "beginner").game_type == "platform_adventure"
    assert provider.generate(
        "A platform adventure where I collect gems and reach the goal", "beginner"
    ).game_type == "platform_adventure"
    assert provider.generate(
        "A timed collection challenge for coins", "beginner"
    ).game_type == "collection_challenge"


def test_forest_platformer_preserves_requested_coins_and_finish_objective() -> None:
    prompt = "A forest platformer with five coins, moving enemies, and a finish flag"
    spec = DevelopmentMockProvider().generate(prompt, "beginner")
    runtime = GameBuilderAgent().build(spec)

    assert spec.game_type == "platform_adventure"
    assert spec.theme == "forest"
    assert spec.palette.background == "#a8d889"
    assert len(spec.levels[0].collectibles) == 5
    assert {item.kind for item in spec.levels[0].collectibles} == {"coin"}
    assert spec.levels[0].collectible_target == 5
    assert spec.win_condition.type == "reach_goal"
    assert spec.levels[0].goal is not None
    assert spec.levels[0].goal.kind == "exit"
    final_platform = spec.levels[0].platforms[-1]
    assert spec.levels[0].goal.position.x == final_platform.x + final_platform.width * 0.8
    assert spec.levels[0].goal.position.y == final_platform.y - 0.04
    assert len(spec.levels[0].enemies) == 2
    assert {enemy.movement for enemy in spec.levels[0].enemies} == {"horizontal"}
    assert len(runtime.levels[0].collectibles) == 5
    GameDesignerAgent._validate_prompt_consistency(prompt, spec)
    GameDesignerAgent._validate_prompt_capabilities(prompt, spec)
    assert GameQAAgent().inspect(runtime).status == "unverified"


def test_provider_builds_distinct_runtime_objects() -> None:
    provider = DevelopmentMockProvider()
    builder = GameBuilderAgent()

    maze = builder.build(provider.generate("A neon maze exploration game", "beginner"))
    target = builder.build(provider.generate("A target arcade challenge", "beginner"))
    top_down = builder.build(provider.generate("A top-down forest collection game", "beginner"))

    assert len(maze.levels[0].walls) >= 2
    assert maze.player.movement_mode == "four_direction"
    assert maze.levels[0].goal is not None
    assert len(target.levels[0].targets) >= 1
    assert top_down.game_type == "top_down_collection"
    assert top_down.levels[0].goal is not None


def test_qa_rejects_maze_without_walls_or_goal() -> None:
    spec = GameSpec.model_validate({
        "title": "Broken maze",
        "description": "Missing maze geometry",
        "game_type": "maze",
        "player": {"start": {"x": 0.1, "y": 0.1}},
        "levels": [{
            "id": "level-1",
            "name": "Maze",
            "platforms": [{"x": 0, "y": 0.9, "width": 1, "height": 0.1}],
        }],
        "win_condition": {"type": "reach_goal"},
        "lose_condition": {"type": "health_depleted"},
    })

    codes = {issue.code for issue in GameQAAgent().inspect(spec).issues}
    assert {"MAZE_WALLS_MISSING", "GOAL_MISSING"} <= codes


def test_qa_rejects_goal_at_player_spawn() -> None:
    spec = GameSpec.model_validate({
        "title": "Immediate win",
        "description": "Goal is at spawn",
        "game_type": "top_down_collection",
        "player": {"start": {"x": 0.1, "y": 0.1}},
        "levels": [{
            "id": "level-1",
            "name": "Forest",
            "platforms": [{"x": 0, "y": 0.9, "width": 1, "height": 0.1}],
            "goal": {"position": {"x": 0.1, "y": 0.1}, "kind": "chest"},
            "collectibles": [{"position": {"x": 0.4, "y": 0.8}}],
            "collectible_target": 1,
        }],
        "win_condition": {"type": "collect_target", "target": 1},
        "lose_condition": {"type": "health_depleted"},
    })

    assert "STARTS_ON_GOAL" in {issue.code for issue in GameQAAgent().inspect(spec).issues}


def test_qa_does_not_apply_platform_collectible_rules_to_maze() -> None:
    spec = GameSpec.model_validate({
        "title": "Maze Collection",
        "description": "Collect keys through corridors.",
        "game_type": "maze",
        "player": {"start": {"x": 0.1, "y": 0.1}},
        "levels": [{
            "id": "level-1",
            "name": "Maze",
            "platforms": [{"x": 0, "y": 0.9, "width": 1, "height": 0.1}],
            "walls": [{"position": {"x": 0.4, "y": 0.1}, "width": 0.1, "height": 0.6}],
            "collectibles": [{"position": {"x": 0.2, "y": 0.2}}],
            "collectible_target": 1,
            "goal": {"position": {"x": 0.9, "y": 0.9}, "kind": "exit"},
        }],
        "win_condition": {"type": "collect_target", "target": 1},
        "lose_condition": {"type": "health_depleted"},
    })

    codes = {issue.code for issue in GameQAAgent().inspect(spec).issues}
    assert "COLLECTIBLES_NOT_ON_PLATFORMS" not in codes
    assert "COLLECTIBLES_IN_MAZE_WALLS" not in codes


def test_genre_fields_validate_and_survive_runtime_building() -> None:
    spec = GameSpec.model_validate({
        "title": "Sky Patrol",
        "description": "A bird dodges pipes.",
        "game_type": "flappy_dodge",
        "camera": "side_scroller",
        "theme": "sky",
        "player": {
            "appearance": "bird",
            "movement_mode": "flap",
            "gravity": 700,
            "flap_strength": 320,
        },
        "levels": [{
            "id": "level-1",
            "name": "Clouds",
            "platforms": [{"x": 0, "y": 0.95, "width": 1, "height": 0.05}],
            "obstacles": [{
                "position": {"x": 0.7, "y": 0.2},
                "width": 0.08,
                "height": 0.4,
                "kind": "pipe",
            }],
        }],
        "win_condition": {"type": "survive", "target": 30},
        "lose_condition": {"type": "health_depleted"},
    })

    runtime = GameBuilderAgent().build(spec)

    assert runtime.game_type == "flappy_dodge"
    assert runtime.camera == "side_scroller"
    assert runtime.player.movement_mode == "flap"
    assert runtime.levels[0].obstacles[0].kind == "pipe"


def test_development_provider_builds_distinct_new_genres() -> None:
    provider = DevelopmentMockProvider()

    flappy = provider.generate("Create a Flappy Bird game with pipes", "beginner")
    shooter = provider.generate("Create a space shooter with lasers", "beginner")
    racing = provider.generate("Create a racing game with road obstacles", "beginner")

    assert flappy.game_type == "flappy_dodge"
    assert flappy.player.movement_mode == "flap"
    assert 200 <= flappy.player.flap_strength <= 250
    assert flappy.levels[0].obstacles
    assert GameQAAgent().inspect(flappy).status == "unverified"
    assert shooter.game_type == "space_shooter"
    assert shooter.win_condition.type == "defeat_target"
    assert shooter.win_condition.target == 8
    assert shooter.fire_control == "space"
    assert shooter.levels[0].hazards
    assert GameQAAgent().inspect(shooter).status == "unverified"
    assert racing.game_type == "racing"
    assert racing.player.movement_mode == "racing"
    assert racing.levels[0].obstacles
    assert racing.levels[0].time_limit == 60


def test_provider_preserves_requested_shooter_defeat_count_and_world_size() -> None:
    provider = DevelopmentMockProvider()
    shooter = provider.generate("Space shooter: defeat five enemy ships", "beginner")
    forest = provider.generate("Forest adventure with water and paths", "beginner")
    legacy = GameSpec.model_validate({
        "title": "Old saved game",
        "description": "A legacy payload without world dimensions.",
        "player": {},
        "levels": [{
            "id": "level-1",
            "name": "Start",
            "platforms": [{"x": 0, "y": 0.9, "width": 1, "height": 0.1}],
        }],
        "win_condition": {"type": "reach_goal"},
        "lose_condition": {"type": "health_depleted"},
    })

    shooter_runtime = GameBuilderAgent().build(shooter)
    forest_runtime = GameBuilderAgent().build(forest)
    legacy_runtime = GameBuilderAgent().build(legacy)

    assert shooter.win_condition.target == 5
    assert shooter_runtime.win_condition == "defeat_target"
    assert shooter_runtime.win_target == 5
    assert forest.world_width > 800
    assert forest.world_height > 480
    assert {item.kind for item in forest.levels[0].scenery} >= {
        "water", "grass", "flower"
    }
    assert forest_runtime.world_width == forest.world_width
    assert legacy_runtime.world_width == 800
    assert legacy_runtime.world_height == 480


def test_qa_rejects_shooter_hazards_that_can_deplete_all_health_in_one_hit() -> None:
    spec = DevelopmentMockProvider().generate(
        "Space shooter: defeat 10 enemy ships", "beginner"
    )
    hazard = spec.levels[0].hazards[0].model_copy(
        update={"damage": spec.player.max_health}
    )
    level = spec.levels[0].model_copy(update={"hazards": [hazard]})
    lethal_spec = spec.model_copy(update={"levels": [level]})

    report = GameQAAgent().inspect(lethal_spec)
    assert report.status == "fail"
    assert any(issue.code == "SHOOTER_DAMAGE_LETHAL" for issue in report.issues)


def test_shooter_qa_requires_defeat_objective_and_positive_target() -> None:
    shooter = DevelopmentMockProvider().generate("A space shooter", "beginner")
    invalid = shooter.model_copy(update={
        "win_condition": WinCondition(type="survive", target=30)
    })

    codes = {issue.code for issue in GameQAAgent().inspect(invalid).issues}

    assert "SHOOTER_OBJECTIVE_INVALID" in codes


def test_flappy_qa_requires_a_matched_upper_and_lower_pipe_pair() -> None:
    flappy = DevelopmentMockProvider().generate("A bird game with pipes", "beginner")
    level = flappy.levels[0].model_copy(update={
        "obstacles": flappy.levels[0].obstacles[:1]
    })
    invalid = flappy.model_copy(update={"levels": [level]})

    codes = {issue.code for issue in GameQAAgent().inspect(invalid).issues}

    assert "FLAPPY_PIPE_PAIR_INVALID" in codes


def test_flappy_qa_rejects_zero_gravity() -> None:
    flappy = DevelopmentMockProvider().generate("A bird game with pipes", "beginner")
    invalid = flappy.model_copy(update={
        "player": flappy.player.model_copy(update={"gravity": 0})
    })

    codes = {issue.code for issue in GameQAAgent().inspect(invalid).issues}

    assert "FLAPPY_GRAVITY_INVALID" in codes


def test_shooter_qa_rejects_an_enemy_overlapping_the_player_spawn() -> None:
    shooter = DevelopmentMockProvider().generate("A space shooter", "beginner")
    hazard = shooter.levels[0].hazards[0].model_copy(update={
        "position": shooter.player.start.model_copy(update={"x": 0.48, "y": 0.8})
    })
    level = shooter.levels[0].model_copy(update={"hazards": [hazard]})
    invalid = shooter.model_copy(update={"levels": [level]})

    codes = {issue.code for issue in GameQAAgent().inspect(invalid).issues}

    assert "SHOOTER_SPAWN_OVERLAP" in codes


def test_forest_qa_rejects_player_start_inside_goal_collision_area() -> None:
    forest = DevelopmentMockProvider().generate("A forest adventure", "beginner")
    level = forest.levels[0].model_copy(update={
        "goal": GoalSpec(position=forest.player.start, kind="chest")
    })
    invalid = forest.model_copy(update={"levels": [level]})

    codes = {issue.code for issue in GameQAAgent().inspect(invalid).issues}

    assert "STARTS_ON_GOAL" in codes


def test_bird_forest_and_shooter_prompts_create_distinct_scene_data() -> None:
    provider = DevelopmentMockProvider()
    builder = GameBuilderAgent()

    bird = builder.build(provider.generate("Create a bird game with pipes", "beginner"))
    forest = builder.build(provider.generate("Create a forest adventure", "beginner"))
    shooter = builder.build(provider.generate("Create a space shooter", "beginner"))

    assert bird.game_type == "flappy_dodge"
    assert bird.theme == "sky"
    assert bird.player.appearance == "bird"
    assert bird.player.movement_mode == "flap"
    assert bird.player.gravity > 0
    assert {obstacle.kind for obstacle in bird.levels[0].obstacles} == {"pipe"}
    assert bird.palette.background != forest.palette.background

    assert forest.game_type == "top_down_collection"
    assert forest.theme == "forest"
    assert forest.player.appearance == "explorer"
    assert forest.player.movement_mode == "four_direction"
    assert forest.player.gravity == 0
    assert {item.kind for item in forest.levels[0].scenery} >= {
        "tree", "bush", "path"
    }
    assert forest.levels[0].collectibles
    assert forest.levels[0].goal is not None

    assert shooter.game_type == "space_shooter"
    assert shooter.theme == "space"
    assert shooter.player.appearance == "ship"
    assert shooter.fire_control == "space"
    assert len(shooter.levels[0].hazards) >= 2
    assert len({bird.palette.background, forest.palette.background, shooter.palette.background}) == 3
    assert len({bird.title, forest.title, shooter.title}) == 3


def test_different_prompts_generate_distinct_runtime_mechanics_and_layouts() -> None:
    provider = DevelopmentMockProvider()
    builder = GameBuilderAgent()
    platformer = builder.build(
        provider.generate(
            "A platformer where I jump between ledges to collect three gems and reach the exit",
            "beginner",
        )
    )
    maze = builder.build(
        provider.generate(
            "A maze where I navigate walls to collect three keys and reach the exit",
            "beginner",
        )
    )
    forest = builder.build(
        provider.generate(
            "A forest exploration game where I collect three coins",
            "beginner",
        )
    )

    assert platformer.game_type == "platform_adventure"
    assert platformer.player.movement_mode == "platformer"
    assert platformer.win_condition == "reach_goal"
    assert platformer.levels[0].platforms[1].y != platformer.levels[0].platforms[2].y
    assert len(platformer.levels[0].collectibles) == 3

    assert maze.game_type == "maze"
    assert maze.camera == "top_down"
    assert maze.player.movement_mode == "four_direction"
    assert maze.win_condition == "reach_goal"
    assert maze.levels[0].walls
    assert maze.levels[0].goal is not None
    assert len(maze.levels[0].collectibles) == 3

    assert forest.game_type == "top_down_collection"
    assert forest.player.movement_mode == "four_direction"
    assert forest.player.appearance == "explorer"
    assert forest.player.gravity == 0
    assert forest.win_condition == "collect_target"
    assert forest.levels[0].scenery
    assert forest.levels[0].goal is not None
    assert len(forest.levels[0].collectibles) == 3

    assert len(
        {
            platformer.game_type,
            maze.game_type,
            forest.game_type,
        }
    ) == 3
    assert maze.levels[0].walls != forest.levels[0].walls


def test_palette_is_validated_and_preserved_by_runtime_builder() -> None:
    spec = DevelopmentMockProvider().generate("A blue forest adventure", "beginner")
    spec = spec.model_copy(update={
        "palette": spec.palette.model_copy(update={"accent": "#123456"})
    })
    runtime = GameBuilderAgent().build(spec)
    assert runtime.palette.accent == "#123456"

    payload = spec.model_dump()
    payload["palette"]["player"] = "yellow"
    try:
        GameSpec.model_validate(payload)
    except ValueError as error:
        assert "palette.player" in str(error)
    else:
        raise AssertionError("Invalid palette colors must be rejected")


def test_development_mock_titles_are_prompt_specific() -> None:
    provider = DevelopmentMockProvider()
    first = provider.generate("A bird game with tall pipes", "beginner")
    second = provider.generate("A bird game with tiny pipes", "beginner")
    assert first.title != second.title


def test_prompt_capability_validation_rejects_mismatched_ai_designs() -> None:
    provider = DevelopmentMockProvider()
    prompts = (
        "A bird game",
        "A forest adventure",
        "A space shooter",
    )
    for prompt in prompts:
        GameDesignerAgent._validate_prompt_capabilities(
            prompt, provider.generate(prompt, "beginner")
        )

    bird = provider.generate("A bird game", "beginner")
    mismatched = bird.model_copy(update={"game_type": "platform_adventure"})
    try:
        GameDesignerAgent._validate_prompt_capabilities("A bird game", mismatched)
    except PromptConsistencyError as error:
        assert "requires flappy_dodge" in str(error)
    else:
        raise AssertionError("A genre mismatch must be rejected")


def test_builder_rejects_out_of_bounds_genre_entities() -> None:
    spec = GameSpec.model_validate({
        "title": "Invalid Track",
        "description": "An obstacle is outside the track.",
        "game_type": "racing",
        "camera": "racing",
        "player": {"movement_mode": "racing"},
        "levels": [{
            "id": "level-1",
            "name": "Track",
            "platforms": [{"x": 0, "y": 0.9, "width": 1, "height": 0.1}],
            "obstacles": [{
                "position": {"x": 0.9, "y": 0.9},
                "width": 0.2,
                "height": 0.1,
                "kind": "cone",
            }],
        }],
        "win_condition": {"type": "reach_goal"},
        "lose_condition": {"type": "health_depleted"},
    })

    try:
        GameBuilderAgent().build(spec)
    except ValueError as error:
        assert "Obstacle extends outside" in str(error)
    else:
        raise AssertionError("Out-of-bounds obstacle should be rejected")
