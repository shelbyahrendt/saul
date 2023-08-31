"""
Contains the definition of the PSD class.
"""

from functools import cache
from pathlib import Path

import matplotlib.pyplot as plt
import numpy as np
from multitaper import MTSpec
from obspy import Stream
from obspy.signal.spectral_estimation import (
    get_idc_infra_hi_noise,
    get_idc_infra_low_noise,
    get_nhnm,
    get_nlnm,
)
from scipy.fft import next_fast_len

# This file downloaded from the supplementary material of Macpherson et al. (2022)
AK_INFRA_NOISE = Path(__file__).with_name('alaska_ambient_infrasound_noise_models.txt')

REFERENCE_VELOCITY = 1  # [m/s] Reference value for seismic PSDs
REFERENCE_PRESSURE = 20e-6  # [Pa] Reference value for infrasound PSDs


def get_ak_infra_noise():
    """Returns the Alaska ambient infrasound noise models from Macpherson et al. (2022).

    Macpherson, K. A., Coffey, J. R., Witsil, A. J., Fee, D., Holtkamp, S., Dalton, S.,
        McFarlin, H., & West, M. (2022). Ambient infrasound noise, station performance,
        and their relation to land cover across Alaska. *Seismological Research
        Letters*, *93*(4), 2239–2258. https://doi.org/10.1785/0220210365

    Returns:
        tuple: Period [s], high noise model [dB rel. 1 Pa^2 Hz^-1], median noise model
        [dB rel. 1 Pa^2 Hz^-1], low noise model [dB rel. 1 Pa^2 Hz^-1]
    """
    f, hnm, mnm, lnm = np.loadtxt(AK_INFRA_NOISE, delimiter=',', skiprows=12).T
    # We convert frequency to period to match the ObsPy functions
    return 1 / f, hnm, mnm, lnm


@cache
def _mtspec(tr_data_tuple, **kwargs):
    """Wrapper around MTSpec to facilitate tuple input (needed for memoization)."""
    return MTSpec(np.array(tr_data_tuple), **kwargs)


class PSD:
    """A class for calculating and plotting PSDs of one or more waveforms."""

    def __init__(self, tr_or_st, time_bandwidth_product=4, number_of_tapers=7):
        """Create a PSD object.

        The PSDs of the input waveforms are estimated in this method. See the
        documentation for `multitaper.mtspec.MTSpec` for details on some of the input
        arguments here.

        Args:
            tr_or_st (Trace or Stream): Input waveforms (response is expected to be
                removed; SAUL expects units of pressure [Pa] for infrasound data and
                velocity [m/s] for seismic data!)
            time_bandwidth_product (float): Time-bandwidth product
            number_of_tapers (int): Number of tapers to use
        """
        # Pre-processing and checks
        self.st = Stream(tr_or_st).copy()  # Always use *copied* Stream objects
        if np.all([tr.stats.channel[1:3] == 'DF' for tr in self.st]):
            self.data_kind = 'infrasound'
        elif np.all([tr.stats.channel[1] == 'H' for tr in self.st]):
            self.data_kind = 'seismic'
        else:
            raise ValueError(
                'Could not determine whether data are infrasound or seismic — or both data kinds are present.'
            )

        # Set reference value for PSD from data kind
        if self.data_kind == 'infrasound':
            self.db_ref_val = REFERENCE_PRESSURE
        else:  # self.data_kind == 'seismic'
            self.db_ref_val = REFERENCE_VELOCITY

        # KEY: Calculate PSD
        self.psd = []
        for tr in self.st:
            mtspec = _mtspec(
                tuple(tr.data),
                nw=time_bandwidth_product,
                kspec=number_of_tapers,  # After a certain point this saturates
                dt=tr.stats.delta,
                nfft=next_fast_len(tr.stats.npts),
            )
            f, pxx = mtspec.rspec()
            f, pxx = f.squeeze(), pxx.squeeze()
            f, pxx = f[1:], pxx[1:]  # Remove DC component
            self.psd.append((f, pxx))

        # Calculate peak frequency of PSD
        self.peak_frequency = []
        for psd in self.psd:
            f, pxx = psd
            self.peak_frequency.append(f[np.argmax(pxx)])

    def __str__(self):
        """A custom string representation of the PSD object."""
        text = f'{len(self.psd)} PSD(s):'
        for tr, peak_f in zip(self.st, self.peak_frequency):
            text += f'\n{tr.id} | {peak_f:.3f} Hz peak frequency'
        return text

    def _repr_pretty_(self, p, cycle):
        """Pretty-printing for IPython usage."""
        p.text(self.__str__())

    def plot(self, show_noise_models=False, infra_noise_model='ak'):
        """Plot the calculated PSDs.

        Args:
            show_noise_models (bool): Whether to plot reference noise models
            infra_noise_model (str): Which infrasound noise model to use (only used if
                `show_noise_models` is True and `self.data_kind` is 'infrasound'), one
                of 'ak' (Alaska noise model) or 'idc' (IMS array noise model)
        """
        fig, ax = plt.subplots()
        for tr, (f, pxx) in zip(self.st, self.psd):
            pxx_db = 10 * np.log10(pxx / (self.db_ref_val**2))
            ax.semilogx(f, pxx_db, label=tr.id)
        if show_noise_models:
            if self.data_kind == 'infrasound':
                if infra_noise_model == 'ak':
                    period, *nms = get_ak_infra_noise()
                    noise_models = [(period, nm) for nm in nms]
                elif infra_noise_model == 'idc':
                    noise_models = [get_idc_infra_low_noise(), get_idc_infra_hi_noise()]
                else:
                    raise ValueError(
                        'Infrasound noise model must be either \'ak\' or \'idc\''
                    )
                # These are all given relative to 1 Pa, so need to convert to `ref_val`
                for i, noise_model in enumerate(noise_models):
                    period, pxx_db_rel_1_pa = noise_model
                    pxx_db_rel_ref_val = pxx_db_rel_1_pa - 10 * np.log10(
                        self.db_ref_val**2
                    )
                    noise_models[i] = period, pxx_db_rel_ref_val
            else:  # self.data_kind == 'seismic'
                noise_models = [get_nlnm(), get_nhnm()]
            xlim, ylim = ax.get_xlim(), ax.get_ylim()  # Store these to restore limits
            for i, noise_model in enumerate(noise_models):
                period, pxx_db = noise_model
                ax.plot(
                    1 / period,
                    pxx_db,
                    color='tab:gray',
                    linestyle=':',
                    zorder=-5,
                    label='Noise model' if not i else None,  # Only label one line
                )
            ax.set_xlim(xlim)
            ax.set_ylim(ylim)
        ax.legend()
        ax.set_xlabel('Frequency (Hz)')
        if self.data_kind == 'infrasound':
            # Convert Pa to µPa
            ylabel = f'Power (dB rel. [{self.db_ref_val * 1e6:g} μPa]$^2$ Hz$^{{-1}}$)'
        else:  # self.data_kind == 'seismic'
            if self.db_ref_val == 1:
                # Special formatting case since 1^2 = 1
                ylabel = f'Power (dB rel. {self.db_ref_val:g} [m s$^{{-1}}$]$^2$ Hz$^{{-1}}$)'
            else:
                ylabel = f'Power (dB rel. [{self.db_ref_val:g} m s$^{{-1}}$]$^2$ Hz$^{{-1}}$)'
        ax.set_ylabel(ylabel)
        fig.tight_layout()
        fig.show()
