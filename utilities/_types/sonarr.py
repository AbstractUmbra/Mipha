from typing import TypedDict

from typing_extensions import NotRequired


__all__ = (
    "SonarrCalendarPayload",
    "SonarrSeriesPayload",
)


class _SonarrImages(TypedDict):
    coverType: str
    url: str


class _SonarrSeasons(TypedDict):
    seasonNumber: int
    monitored: bool


class _SonarrEpisodeFileQualityQualityPayload(TypedDict):
    id: int
    name: str
    source: str
    resolution: int


class _SonarrEpisodeFileQualityRevisionPayload(TypedDict):
    version: int
    real: int
    isRepack: bool


class _SonarrEpisodeFileQualityPayload(TypedDict):
    quality: _SonarrEpisodeFileQualityQualityPayload
    revision: _SonarrEpisodeFileQualityRevisionPayload


class _SonarrEpisodeFileLanguagePayload(TypedDict):
    id: int
    name: str


class _SonarrEpisodeFileMediaInfoPayload(TypedDict):
    audioChannels: float
    audioCodec: str
    videoCodec: str


class SonarrEpisodeFilePayload(TypedDict):
    seriesId: int
    seasonNumber: int
    relativePath: str
    path: str
    size: int
    dateAdded: str
    sceneName: str
    quality: _SonarrEpisodeFileQualityPayload
    language: _SonarrEpisodeFileLanguagePayload
    mediaInfo: _SonarrEpisodeFileMediaInfoPayload
    originalFilePath: str
    qualityCutoffNotMet: bool
    id: int


class _SonarrSeriesRatings(TypedDict):
    votes: int
    value: float


class SonarrSeriesPayload(TypedDict):
    title: str
    sortTitle: str
    seasonCount: int
    status: str
    overview: str
    network: str
    airTime: str
    images: list[_SonarrImages]
    seasons: list[_SonarrSeasons]
    year: int
    path: str
    profileId: int
    languageProfileId: int
    seasonFolder: bool
    monitored: bool
    useSceneNumbering: bool
    runtime: int
    tvdbId: int
    tvRageId: int
    tvMazeId: int
    firstAired: str
    lastInfoSync: str
    seriesTyped: str
    cleanTitle: str
    imdbId: str
    titleSlug: str
    certification: str
    genres: list[str]
    tags: list[str]
    added: str  # UTC datetime
    ratings: _SonarrSeriesRatings
    qualityProfileId: int
    id: int


class SonarrCalendarPayload(TypedDict):
    seriesId: int
    episodeFileId: int
    seasonNumber: int
    episodeNumber: int
    title: str
    airDate: str  # UTC Timezone
    airDateUtc: str  # UTC Timezone
    overview: str
    episodeFile: NotRequired[SonarrEpisodeFilePayload]
    hasFile: bool
    monitored: bool
    unverifiedSceneNumbering: bool
    series: SonarrSeriesPayload
    lastSearchTime: str
    id: int
