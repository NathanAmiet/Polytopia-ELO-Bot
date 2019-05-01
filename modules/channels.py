import discord
# import asyncio
# from discord.ext import commands
import settings
# import peewee
# import modules.models as models
import modules.exceptions as exceptions
import logging

logger = logging.getLogger('polybot.' + __name__)


def generate_channel_name(game_id, game_name: str, team_name: str = None):
    # Turns game named 'The Mountain of Fire' to something like #e41-mountain-of-fire_ronin

    if not game_name:
        game_name = 'No Name'
        logger.warn(f'No game name passed to generate_channel_name for game {game_id}')
    if not team_name:
        logger.info(f'No team name passed to generate_channel_name for game {game_id}')
        team_name = ''

    game_team = f'{game_name.replace("the ","").replace("The ","")}_{team_name.replace("the ","").replace("The ","")}'.strip('_')

    if game_name.lower()[:2] == 's5' or game_name.lower()[:2] == 's4':
        # hack to have special naming for season 3 or season 4 games, named eg 'S3W1 Mountains of Fire'. Makes channel easier to see
        chan_name = f'{" ".join(game_team.split()).replace(" ", "-")}-e{game_id}'
    elif game_name.upper()[:3] == 'WWN' or game_name.upper()[:2] == 'WWN':
        chan_name = f'{" ".join(game_team.split()).replace(" ", "-")}-e{game_id}'
    else:
        chan_name = f'e{game_id}-{" ".join(game_team.split()).replace(" ", "-")}'
    return chan_name


def get_channel_category(guild, team_name: str = None):
    # Returns (DiscordCategory, Bool_IsTeamCategory?) or None
    # Bool_IsTeamCategory? == True if its using a team-specific category, False if using a central games category

    if guild.me.guild_permissions.manage_channels is not True:
        logger.error('manage_channels permission is false.')
        return None, None

    if team_name:
        team_name = team_name.lower().replace('the', '').strip()  # The Ronin > ronin
        for cat in guild.categories:
            if team_name in cat.name.lower():
                logger.debug(f'Using {cat.id} - {cat.name} as a team channel category')
                return cat, True

    # No team category found - using default category. ie. intermingled home/away games or channel for entire game

    for game_channel_category in settings.guild_setting(guild.id, 'game_channel_categories'):

        chan_category = discord.utils.get(guild.categories, id=int(game_channel_category))
        if chan_category is None:
            logger.warn(f'chans_category_id {game_channel_category} was supplied but cannot be loaded')
            continue

        if len(chan_category.channels) >= 50:
            logger.warn(f'chans_category_id {game_channel_category} was supplied but is full')
            continue

        logger.debug(f'using {chan_category.id} - {chan_category.name} for channel category')
        return chan_category, False  # Successfully use this chan_category

    logger.error('could not successfully load a channel category')
    return None, None


async def create_game_channel(guild, game, player_list, team_name: str = None, using_team_server_flag: bool = False):
    chan_cat, team_cat_flag = get_channel_category(guild, team_name)
    if chan_cat is None:
        logger.error(f'in create_squad_channel - cannot proceed due to None category')
        return None

    if game.name.upper()[:3] == 'WWN' and guild.id == settings.server_ids['polychampions']:
        # TODO: Remove when World War Newt event is over Q2 2019
        wwn_category = discord.utils.get(guild.categories, id=510403013391679498)
        if wwn_category:
            chan_cat, team_cat_flag = wwn_category, False

    chan_name = generate_channel_name(game_id=game.id, game_name=game.name, team_name=team_name)
    chan_members = [guild.get_member(p.discord_member.discord_id) for p in player_list]

    if team_cat_flag or using_team_server_flag:
        # Channel is going into team-specific category, so let its permissions sync
        chan_permissions = None
    else:
        # Both chans going into a central ELO Games category. Give them special permissions so only game players can see chan

        chan_permissions = {}
        perm = discord.PermissionOverwrite(read_messages=True, add_reactions=True, send_messages=True, attach_files=True, manage_messages=True)

        for m in chan_members + [guild.me]:
            chan_permissions[m] = perm

        chan_permissions[guild.default_role] = discord.PermissionOverwrite(read_messages=False)

    try:
        new_chan = await guild.create_text_channel(name=chan_name, overwrites=chan_permissions, category=chan_cat, reason='ELO Game chan')
    except (discord.errors.Forbidden, discord.errors.HTTPException) as e:
        logger.error(f'Exception in create_game_channels:\n{e} - Status {e.status}, Code {e.code}: {e.text}')
        raise exceptions.MyBaseException(e)
        # return None
    except discord.errors.InvalidArgument as e:
        logger.error(f'Exception in create_game_channels:\n{e}')
        raise exceptions.MyBaseException(e)
        # return None
    logger.debug(f'Created channel {new_chan.name}')

    return new_chan


async def greet_game_channel(guild, chan, roster_names, game, player_list, full_game: bool = False):

    chan_mentions = [f'<@{p.discord_member.discord_id}>' for p in player_list]

    if full_game:
        allies_str = f'Participants in this game are {" / ".join(chan_mentions)}\n'
        chan_type_str = '**full game channel**'
    else:
        allies_str = f'Your teammates are {" / ".join(chan_mentions)}\n'
        chan_type_str = '**allied team channel**'

    if game.host or game.notes:
        match_content = f'Game hosted by **{game.host.name}**\n' if game.host else ''
        match_content = match_content + f'**Notes:** {game.notes}\n' if game.notes else match_content
    else:
        match_content = ''
    try:
        await chan.send(f'This is the {chan_type_str} for game **{game.name}**, ID {game.id}.\n{allies_str}'
            f'The teams for this game are:\n{roster_names}\n\n'
            f'{match_content}'
            '*This channel will self-destruct soon after the game is marked as concluded.*')
    except (discord.errors.Forbidden, discord.errors.HTTPException) as e:
        logger.error(f'Could not send to created channel:\n{e} - Status {e.status}, Code {e.code}: {e.text}')


async def delete_game_channel(guild, channel_id: int):

    chan = guild.get_channel(channel_id)
    if chan is None:
        return logger.warn(f'Channel ID {channel_id} provided for deletion but it could not be loaded from guild')
    try:
        logger.warn(f'Deleting channel {chan.name}')
        await chan.delete(reason='Game concluded')
    except discord.DiscordException as e:
        logger.error(f'Could not delete channel: {e}')


async def send_message_to_channel(guild, channel_id: int, message: str):
    chan = guild.get_channel(channel_id)
    if chan is None:
        return logger.warn(f'Channel ID {channel_id} provided for message but it could not be loaded from guild')

    try:
        await chan.send(message)
    except discord.DiscordException as e:
        logger.error(f'Could not delete channel: {e}')


async def update_game_channel_name(guild, channel_id: int, game_id: int, game_name: str, team_name: str = None):
    chan = guild.get_channel(channel_id)
    if chan is None:
        return logger.warn(f'Channel ID {channel_id} provided for update but it could not be loaded from guild')

    chan_name = generate_channel_name(game_id=game_id, game_name=game_name, team_name=team_name)

    if chan_name.lower() == chan.name.lower():
        return logger.debug(f'Newly-generated channel name for channel {channel_id} game {game_id} is the same - no change to channel.')

    try:
        await chan.edit(name=chan_name, reason='Game renamed')
        logger.info(f'Renamed channel for game {game_id} to {chan_name}')
    except discord.DiscordException as e:
        logger.error(f'Could not delete channel: {e}')

    await chan.send(f'This game has been renamed to *{game_name}*.')
