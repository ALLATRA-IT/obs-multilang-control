import json
import logging
from typing import List

from atom.containerlist import ContainerList
from atom.dict import Dict
from atom.atom import Atom
from atom.instance import Instance
from atom.scalars import Unicode, Bool, Int, Float
from obswebsocket import obsws, requests, events

DEFAULT_HOST = "127.0.0.1"
DEFAULT_PORT = 4444
ORIGINAL_LANG = "Original"


def _create_connection(host, port, password=None):
    ws = obsws(host, port, password)
    try:
        ws.connect()
    except Exception as e:
        logging.exception(e)
        return None
    return ws


def _current_obs_lang(ws):
    profile_name = ws.call(requests.GetCurrentProfile()).datain["profile-name"]
    scene_source_name = ws.call(requests.GetCurrentSceneCollection()).datain["sc-name"]
    if profile_name != scene_source_name:
        ws.call(requests.SetCurrentSceneCollection(profile_name))
    return profile_name


def _current_obs_scene(ws):
    scenes = ws.call(requests.GetSceneList()).datain["scenes"]
    if len(scenes) > 1:
        raise ValueError("Only one `Scene` should be present in OBS")
    if scenes[0]["name"].lower() != "scene":
        raise ValueError("Scene should have name `Scene`")
    return scenes[0]


def _current_obs_stream_settings(ws):
    settings = ws.call(requests.GetStreamSettings())
    return settings.datain


class ObsInstanceModel(Atom):
    ws: obsws = Instance(obsws)
    lang_code = Unicode()
    scene_name = Unicode()
    origin_source = Dict()
    trans_source = Dict()
    host = Unicode(default=DEFAULT_HOST)
    port = Int(default=DEFAULT_PORT)
    is_connected = Bool()
    is_origin_audio = Bool()
    switch_triggered = Bool()

    is_stream_started = Bool()
    is_audio_muted = Bool()
    # stream_server_url = Unicode()
    # stream_key = Unicode()
    # stream_settings = Dict(dict(type='rtmp_common', save=True, settings=dict(ser)))
    origin_volume_level_on_origin = Float(1.0)

    origin_volume_level_on_trans = Float(0.20)
    trans_volume_level_on_trans = Float(1.0)

    def refresh_sources(self):
        self.ws.call(
            requests.SetSourceSettings(self.origin_source["name"], self.origin_source)
        )
        self.ws.call(
            requests.SetSourceSettings(self.trans_source["name"], self.trans_source)
        )

    def _change_volume(self, name, volume):
        self.switch_triggered = True
        self.ws.call(requests.SetVolume(name, volume))

    def switch_to_origin(self):
        self._change_volume(
            self.origin_source["name"], self.origin_volume_level_on_origin
        )
        self._change_volume(self.trans_source["name"], 0.0)
        self.is_origin_audio = True

    def switch_to_translation(self):
        self._change_volume(
            self.origin_source["name"], self.origin_volume_level_on_trans
        )
        self._change_volume(self.trans_source["name"], self.trans_volume_level_on_trans)
        self.is_origin_audio = False

    def start_stream(self):
        self.ws.call(requests.StartStreaming())

    def stop_stream(self):
        self.ws.call(requests.StopStreaming())

    def connect(self, host=None, port=None):
        if self.is_connected:
            return True
        if host and port:
            self.host = host
            self.port = port
        ws = _create_connection(self.host, self.port)
        if ws is None:
            return False
        self.ws = ws
        self._populate_data()
        self._register_callbacks()
        self.is_connected = True
        return self.is_connected

    def _register_callbacks(self):
        def handle_volume(e: events.SourceVolumeChanged):
            """Save volume level changed from OBS"""
            if self.switch_triggered:
                self.switch_triggered = False
                return
            if e.getSourcename() == self.origin_source["name"]:
                if self.is_origin_audio:
                    self.origin_volume_level_on_origin = e.getVolume()
                else:
                    self.origin_volume_level_on_trans = e.getVolume()
            elif e.getSourcename() == self.trans_source["name"]:
                if not self.is_origin_audio:
                    self.trans_volume_level_on_trans = e.getVolume()

        def handle_streaming_status(e: events.StreamStatus):
            if isinstance(e, events.StreamStopped):
                self.is_stream_started = False
                return
            self.is_stream_started = e.getStreaming()

        def handle_exiting(e: events.Exiting):
            self.is_connected = False
            self.is_stream_started = False

        self.ws.register(handle_volume, events.SourceVolumeChanged)
        self.ws.register(handle_streaming_status, events.StreamStatus)
        self.ws.register(handle_streaming_status, events.StreamStopped)
        self.ws.register(handle_exiting, events.Exiting)

    def _populate_data(self):
        self.lang_code = _current_obs_lang(self.ws)
        scene = _current_obs_scene(self.ws)
        for source in scene["sources"]:
            if source["name"] == "Origin VA":
                self.origin_source = source
            elif source["name"] == f"{self.lang_code} Translation":
                self.trans_source = source
        self.scene_name = scene["name"]
        self.mute_audio()

    # settings = _current_obs_stream_settings(self.ws)
    # if settings["type"] == "rtmp_common":
    #     self.stream_key = settings["settings"]["key"]
    #     self.stream_server_url = settings["settings"]["server"]

    def _set_mute(self, source_name, mute):
        self.ws.call(requests.SetMute(source_name, mute))

    def mute_audio(self):
        self._set_mute(self.origin_source["name"], True)
        self._set_mute(self.trans_source["name"], True)
        self.is_audio_muted = True

    def unmute_audio(self):
        self._set_mute(self.origin_source["name"], False)
        self._set_mute(self.trans_source["name"], False)
        self.is_audio_muted = False

    def disconnect(self):
        if not self.is_connected:
            return self.is_connected
        self.ws.disconnect()
        self.is_connected = False
        return self.is_connected

    def __setstate__(self, state):
        self.host = state["host"]
        self.port = state["port"]
        self.origin_volume_level_on_origin = state["origin_volume_level_on_origin"]
        self.origin_volume_level_on_trans = state["origin_volume_level_on_trans"]
        self.trans_volume_level_on_trans = state["trans_volume_level_on_trans"]
        if not self.is_connected and state["is_connected"]:
            self.connect()

    def __getstate__(self):
        return dict(
            host=self.host,
            port=self.port,
            is_connected=self.is_connected,
            origin_volume_level_on_origin=self.origin_volume_level_on_origin,
            origin_volume_level_on_trans=self.origin_volume_level_on_trans,
            trans_volume_level_on_trans=self.trans_volume_level_on_trans,
        )


class ObsManagerModel(Atom):
    current_lang_code = Unicode()
    obs_instances: List[ObsInstanceModel] = ContainerList(default=[ObsInstanceModel()])
    state_path = Unicode()
    status = Unicode()

    def add_obs_instance(self, obs_or_host=None, port=None):
        if isinstance(obs_or_host, ObsInstanceModel):
            obs = obs_or_host
        elif obs_or_host and port:
            obs = ObsInstanceModel(host=obs_or_host, port=port)
        else:
            obs = ObsInstanceModel()
        if obs.port != DEFAULT_PORT and obs.port in [
            o.port for o in self.obs_instances
        ]:
            self.status = f"OBS {obs.port} already added"
            logging.info(self.status)
            return obs
        self.obs_instances.append(obs)
        self.status = f"OBS configuration with address {obs.host}:{obs.port} created!"
        return obs

    def pop_obs_instance(self):
        obs = self.obs_instances.pop()
        obs.disconnect()

    def __getstate__(self):
        return dict(
            current_lang_code=self.current_lang_code,
            obs_instances=[o.__getstate__() for o in self.obs_instances],
        )

    def __setstate__(self, state):
        self.current_lang_code = state["current_lang_code"]
        self.obs_instances.clear()
        for obs_data in state["obs_instances"]:
            obs = ObsInstanceModel()
            obs.__setstate__(obs_data)
            self.add_obs_instance(obs)

    def switch_to_lang(self, next_lang_code):
        if next_lang_code == self.current_lang_code:
            logging.info(f"Already at {next_lang_code}")
            return
        next_obs = None
        for obs in self.obs_instances:
            if next_lang_code == ORIGINAL_LANG:
                obs.switch_to_origin()
                continue
            if obs.lang_code == next_lang_code:
                obs.switch_to_origin()
                next_obs = obs
                logging.info(f"OBS {obs.lang_code} was switched to ORIGIN sound")
            elif obs.lang_code == self.current_lang_code:
                obs.switch_to_translation()
                logging.info(f"OBS {obs.lang_code} was switched to TRANSLATION sound")
        self.status = f"Switched from {self.current_lang_code} to {next_lang_code}!"
        self.current_lang_code = next_lang_code
        return next_obs

    def save_state(self):
        with open(self.state_path, "w") as f:
            json.dump(self.__getstate__(), f)

    def restore_state(self):
        with open(self.state_path, "r") as f:
            data = json.load(f)
            self.__setstate__(data)

    def connect_all(self):
        for o in self.obs_instances:
            o.connect()

    def disconnect_all(self):
        for o in self.obs_instances:
            o.disconnect()

    def start_streams(self):
        for o in self.obs_instances:
            o.start_stream()

    def stop_streams(self):
        for o in self.obs_instances:
            o.stop_stream()

    def mute_audios(self):
        for o in self.obs_instances:
            o.mute_audio()

    def unmute_audios(self):
        for o in self.obs_instances:
            o.unmute_audio()
