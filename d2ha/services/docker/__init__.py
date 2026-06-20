from .base import DockerBase
from .containers import DockerContainersMixin
from .images_updates import DockerImagesUpdatesMixin
from .networks import DockerNetworksMixin
from .volumes import DockerVolumesMixin
from .system import DockerSystemMixin
from .events import DockerEventsMixin

class DockerService(
    DockerContainersMixin,
    DockerImagesUpdatesMixin,
    DockerNetworksMixin,
    DockerVolumesMixin,
    DockerSystemMixin,
    DockerEventsMixin,
    DockerBase
):
    """Main facade for all Docker operations, built from mixins."""
    pass
