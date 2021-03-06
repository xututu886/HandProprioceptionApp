import math, time
import numpy as np
from random import randrange
from kivy.uix.screenmanager import Screen, ScreenManager, FadeTransition
from kivy.core.window import Window
from kivy.app import App
from kivy.properties import ObjectProperty
from kivy.uix.popup import Popup
from kivy.uix.floatlayout import FloatLayout
from kivy.storage.jsonstore import JsonStore
from kivy import platform
import threading

Window.fullscreen = 'auto'

def cartesian(arrays, out=None):
    """Generate a cartesian product of input arrays.

    Parameters
    -----------------
    arrays: list of array-like 
        1-D arrays to form the cartesian product of.
    out: ndarray
        Array to place the cartesian product in.

    Returns 
    -----------------
    out: ndarray
        2-D array of shape (M, len(arrays)) containing cartesian products
        formed of input arrays.

    """
    arrays = [np.asarray(x) for x in arrays]
    shape = (len(x) for x in arrays)
    dtype = arrays[0].dtype

    ix = np.indices(shape)
    ix = ix.reshape(len(arrays), -1).T

    if out is None:
        out = np.empty_like(ix, dtype = dtype)

    for n, arr in enumerate(arrays):
        out[:, n] = arrays[n][ix[:,n]]

    return out

"""
Copyright © 2016, N. Niehof, Radboud University Nijmegen

PsiMarginal is free software: you can redistribute it and/or modify
it under the terms of the GNU General Public License as published by
the Free Software Foundation, either version 3 of the License, or
(at your option) any later version.

PsiMarginal is distributed in the hope that it will be useful,
but WITHOUT ANY WARRANTY; without even the implied warranty of
MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE.  See the
GNU General Public License for more details.

You should have received a copy of the GNU General Public License
along with PsiMarginal. If not, see <http://www.gnu.org/licenses/>.
"""

def pf(parameters, psyfun='cGauss'):
    """Generate conditional probabilities from psychometric function.

    Arguments
    ---------
        parameters: ndarray (float64) containing parameters as columns
            mu   : threshold

            sigma    : slope

            gamma   : guessing rate (optional), default is 0.2

            lambda  : lapse rate (optional), default is 0.04

            x       : stimulus intensity

        psyfun  : type of psychometric function.
                'cGauss' cumulative Gaussian

                'Gumbel' Gumbel, aka log Weibull

    Returns
    -------
    1D-array of conditional probabilities p(response | mu,sigma,gamma,lambda,x)
    """

    # Unpack parameters
    if np.size(parameters, 1) == 5:
        [mu, sigma, gamma, llambda, x] = np.transpose(parameters)
    elif np.size(parameters, 1) == 4:
        [mu, sigma, llambda, x] = np.transpose(parameters)
        gamma = llambda
    elif np.size(parameters, 1) == 3:
        [mu, sigma, x] = np.transpose(parameters)
        gamma = 0.2
        llambda = 0.04
    else:  # insufficient number of parameters will give a flat line
        psyfun = None
        gamma = 0.2
        llambda = 0.04
    # Psychometric function
    ones = np.ones(np.shape(mu))
    if psyfun == 'cGauss':
        # F(x; mu, sigma) = Normcdf(mu, sigma) = 1/2 * erfc(-sigma * (x-mu) /sqrt(2))
        z = np.divide(np.subtract(x, mu), sigma)
        p = 0.5 * np.array([math.erfc(-zi / np.sqrt(2)) for zi in z])
    elif psyfun == 'Gumbel':
        # F(x; mu, sigma) = 1 - exp(-10^(sigma(x-mu)))
        p = ones - np.exp(-np.power((np.multiply(ones, 10.0)), (np.multiply(sigma, (np.subtract(x, mu))))))
    elif psyfun == 'Weibull':
        # F(x; mu, sigma)
        p = 1 - np.exp(-(np.divide(x, mu)) ** sigma)
    else:
        # flat line if no psychometric function is specified
        p = np.ones(np.shape(mu))
    y = gamma + np.multiply((ones - gamma - llambda), p)
    return y

class Psi:
    """Find the stimulus intensity with minimum expected entropy for each trial, to determine the psychometric function.

    Psi adaptive staircase procedure for use in psychophysics.

    Arguments
    ---------
        stimRange :
            range of possible stimulus intensities.

        Pfunction (str) : type of psychometric function to use.
            'cGauss' cumulative Gaussian

            'Gumbel' Gumbel, aka log Weibull

        nTrials :
            number of trials

        threshold :
            (alpha) range of possible threshold values to search

        thresholdPrior (tuple) : type of prior probability distribution to use.
            Also: slopePrior, guessPrior, lapsePrior.

            ('normal',0,1): normal distribution, mean and standard deviation.

            ('uniform',None) : uniform distribution, mean and standard deviation not defined.

        slope :
            (sigma) range of possible slope values to search

        slopePrior :
            see thresholdPrior

        guessRate :
            (gamma) range of possible guessing rate values to search

        guessPrior :
            see thresholdPrior

        lapseRate :
            (lambda) range of possible lapse rate values to search

        lapsePrior :
            see thresholdPrior

        marginalize (bool) :
            If True, marginalize out the lapse rate and guessing rate before finding the stimulus
            intensity of lowest expected entropy. This uses the Prins (2013) method to include the guessing and lapse rate
            into the probability disctribution. These rates are then marginalized out, and only the threshold and slope are included
            in selection of the stimulus intensity.

            If False, lapse rate and guess rate are included in the selection of stimulus intensity.

    How to use
    ----------
        Create a psi object instance with all relevant arguments. Selecting a correct search space for the threshold,
        slope, guessing rate and lapse rate is important for the psi procedure to function well. If an estimate for
        one of the parameters ends up at its (upper or lower) limit, the result is not reliable, and the procedure
        should be repeated with a larger search range for that parameter.

        Example:
            >>> s   = range(-5,5) # possible stimulus intensities
            obj = Psi(s)

        The stimulus intensity to be used in the current trial can be found in the field xCurrent.

        Example:
            >>> stim = obj.xCurrent
        NOTE: if obj.xCurrent returns None, the calculation is not yet finished.
        This can be avoided by waiting until xCurrent has a numeric value, e.g.:
            >>> while obj.xCurrent == None:
                    pass # hang in this loop until the psi calculation has finished
                stim = obj.xCurrent

        After each trial, update the psi staircase with the subject response, by calling the addData method.

        Example:
            >>> obj.addData(resp)
    """

    def __init__(self, stimRange, Pfunction='cGauss', nTrials=50, threshold=None, thresholdPrior=('uniform', None),
                 slope=None, slopePrior=('uniform', None),
                 guessRate=None, guessPrior=('uniform', None), lapseRate=None, lapsePrior=('uniform', None),
                 marginalize=True, thread=True):

        # Psychometric function parameters
        self.stimRange = stimRange  # range of stimulus intensities
        self.version = 1.0
        self.threshold = np.arange(-10, 10, 0.1)
        self.slope = np.arange(0.005, 20, 0.1)
        self.guessRate = np.arange(0.0, 0.11, 0.05)
        self.lapseRate = np.arange(0.0, 0.11, 0.05)
        self.marginalize = marginalize  # marginalize out nuisance parameters gamma and lambda?
        self.psyfun = Pfunction
        self.thread = thread

        if threshold is not None:
            self.threshold = threshold
            if np.shape(self.threshold) == ():
                self.threshold = np.expand_dims(self.threshold, 0)
        if slope is not None:
            self.slope = slope
            if np.shape(self.slope) == ():
                self.slope = np.expand_dims(self.slope, 0)
        if guessRate is not None:
            self.guessRate = guessRate
            if np.shape(self.guessRate) == ():
                self.guessRate = np.expand_dims(self.guessRate, 0)
        if lapseRate is not None:
            self.lapseRate = lapseRate
            if np.shape(self.lapseRate) == ():
                self.lapseRate = np.expand_dims(self.lapseRate, 0)

        # Priors
        self.thresholdPrior = thresholdPrior
        self.slopePrior = slopePrior
        self.guessPrior = guessPrior
        self.lapsePrior = lapsePrior

        self.priorMu = self.__genprior(self.threshold, *thresholdPrior)
        self.priorSigma = self.__genprior(self.slope, *slopePrior)
        self.priorGamma = self.__genprior(self.guessRate, *guessPrior)
        self.priorLambda = self.__genprior(self.lapseRate, *lapsePrior)

        # if guess rate equals lapse rate, and they have equal priors,
        # then gamma can be left out, as the distributions will be the same
        self.gammaEQlambda = all((all(self.guessRate == self.lapseRate), all(self.priorGamma == self.priorLambda)))
        # likelihood: table of conditional probabilities p(response | alpha,sigma,gamma,lambda,x)
        # prior: prior probability over all parameters p_0(alpha,sigma,gamma,lambda)
        if self.gammaEQlambda:
            self.dimensions = (len(self.threshold), len(self.slope), len(self.lapseRate), len(self.stimRange))
            self.likelihood = np.reshape(
                pf(cartesian((self.threshold, self.slope, self.lapseRate, self.stimRange)), psyfun=Pfunction), self.dimensions)
            # row-wise products of prior probabilities
            self.prior = np.reshape(
                np.prod(cartesian((self.priorMu, self.priorSigma, self.priorLambda)), axis=1), self.dimensions[:-1])
        else:
            self.dimensions = (len(self.threshold), len(self.slope), len(self.guessRate), len(self.lapseRate), len(self.stimRange))
            self.likelihood = np.reshape(
                pf(cartesian((self.threshold, self.slope, self.guessRate, self.lapseRate, self.stimRange)), psyfun=Pfunction), self.dimensions)
            # row-wise products of prior probabilities
            self.prior = np.reshape(
                np.prod(cartesian((self.priorMu, self.priorSigma, self.priorGamma, self.priorLambda)), axis=1), self.dimensions[:-1])

        # normalize prior
        self.prior = self.prior / np.sum(self.prior)

        # Set probability density function to prior
        self.pdf = np.copy(self.prior)

        # settings
        self.iTrial = 0
        self.nTrials = nTrials
        self.stop = 0
        self.response = []
        self.stim = []

        # Generate the first stimulus intensity
        self.minEntropyStim()

    def __genprior(self, x, distr='uniform', mu=0, sig=1):
        """Generate prior probability distribution for variable.

        Arguments
        ---------
            x   :  1D numpy array (float64)
                    points to evaluate the density at.

            distr :  string
                    Distribution to use a prior :
                        'uniform'   (default) discrete uniform distribution

                        'normal'   normal distribution

                        'gamma'    gamma distribution

                        'beta'     beta distribution

            mu :  scalar float
                first parameter of distr distribution (check scipy for parameterization)

            sig : scalar float
                second parameter of distr distribution

        Returns
        -------
        1D numpy array of prior probabilities (unnormalized)
        """
        if distr == 'uniform':
            nx = len(x)
            p = np.ones(nx) / nx
        elif distr == 'normal':
            p = np.exp(-(x-mu)**2 / (2.0*(sig)**2)) / np.sqrt(2.0*np.pi*(sig)**2)
        elif distr == 'beta':
            OnePx = (sig - 1.0) * np.log1p(-x) + (mu - 1.0) * np.log(x)
            beta = math.gamma(mu) * math.gamma(sig) / math.gamma(mu + sig)
            OnePx -= np.log(np.abs(beta))
            p = np.exp(OnePx)
        elif distr == 'gamma':
            p = x ** (mu - 1) * (np.exp(-x)) / math.gamma(sig)
        else:
            nx = len(x)
            p = np.ones(nx) / nx
        return p

    def meta_data(self):
        import time
        import sys
        metadata = {}
        date = time.strftime("%Y-%m-%d %H:%M:%S", time.gmtime(time.time()))
        metadata['date'] = date
        metadata['Version'] = self.version
        metadata['Python Version'] = sys.version
        metadata['Numpy Version'] = np.__version__
        metadata['Scipy Version '] = scipy.__version__
        metadata['psyFunction'] = self.psyfun
        metadata['thresholdGrid'] = self.threshold.tolist()
        metadata['thresholdPrior'] = self.thresholdPrior
        metadata['slopeGrid'] = self.slope.tolist()
        metadata['slopePrior'] = self.slopePrior
        metadata['gammaGrid'] = self.guessRate.tolist()
        metadata['gammaPrior'] = self.guessPrior
        metadata['lapseGrid'] = self.lapseRate.tolist()
        metadata['lapsePrior'] = self.lapsePrior
        return metadata

    def __entropy(self, pdf):
        """Calculate shannon entropy of posterior distribution.
        Arguments
        ---------
            pdf :   ndarray (float64)
                    posterior distribution of psychometric curve parameters for each stimuli


        Returns
        -------
        1D numpy array (float64) : Shannon entropy of posterior for each stimuli
        """
        # Marginalize out all nuisance parameters, i.e. all except alpha and sigma
        postDims = np.ndim(pdf)
        if self.marginalize == True:
            while postDims > 3:  # marginalize out second-to-last dimension, last dim is x
                pdf = np.sum(pdf, axis=-2)
                postDims -= 1
        # find expected entropy, suppress divide-by-zero and invalid value warnings
        # as this is handled by the NaN redefinition to 0
        with np.errstate(divide='ignore', invalid='ignore'):
            entropy = np.multiply(pdf, np.log(pdf))
        entropy[np.isnan(entropy)] = 0  # define 0*log(0) to equal 0
        dimSum = tuple(range(postDims - 1))  # dimensions to sum over. also a Chinese dish
        entropy = -(np.sum(entropy, axis=dimSum))
        return entropy

    def minEntropyStim(self):
        """Find the stimulus intensity based on the expected information gain.

        Minimum Shannon entropy is used as selection criterion for the stimulus intensity in the upcoming trial.
        """
        self.pdf = self.pdf
        self.nX = len(self.stimRange)
        self.nDims = np.ndim(self.pdf)

        # make pdf the same dims as conditional prob table likelihood
        self.pdfND = np.expand_dims(self.pdf, axis=self.nDims)  # append new axis
        self.pdfND = np.tile(self.pdfND, (self.nX))  # tile along new axis

        # Probabilities of response r (succes, failure) after presenting a stimulus
        # with stimulus intensity x at the next trial, multiplied with the prior (pdfND)
        self.pTplus1success = np.multiply(self.likelihood, self.pdfND)
        self.pTplus1failure = self.pdfND - self.pTplus1success

        # Probability of success or failure given stimulus intensity x, p(r|x)
        self.sumAxes = tuple(range(self.nDims))  # sum over all axes except the stimulus intensity axis
        self.pSuccessGivenx = np.sum(self.pTplus1success, axis=self.sumAxes)
        self.pFailureGivenx = np.sum(self.pTplus1failure, axis=self.sumAxes)

        # Posterior probability of parameter values given stimulus intensity x and response r
        # p(alpha, sigma | x, r)
        self.posteriorTplus1success = self.pTplus1success / self.pSuccessGivenx
        self.posteriorTplus1failure = self.pTplus1failure / self.pFailureGivenx

        # Expected entropy for the next trial at intensity x, producing response r
        self.entropySuccess = self.__entropy(self.posteriorTplus1success)
        self.entropyFailure = self.__entropy(self.posteriorTplus1failure)
        self.expectEntropy = np.multiply(self.entropySuccess, self.pSuccessGivenx) + np.multiply(self.entropyFailure,
                                                                                                 self.pFailureGivenx)
        self.minEntropyInd = np.argmin(self.expectEntropy)  # index of smallest expected entropy
        self.xCurrent = self.stimRange[self.minEntropyInd]  # stim intensity at minimum expected entropy

        self.iTrial += 1
        if self.iTrial == (self.nTrials - 1):
            self.stop = 1

    def addData(self, response):
        """
        Add the most recent response to start calculating the next stimulus intensity

        Arguments
        ---------
            response: (int)
                1: correct/right

                0: incorrect/left
        """
        self.stim.append(self.xCurrent)
        self.response.append(response)

        self.xCurrent = None

        # Keep the posterior probability distribution that corresponds to the recorded response
        if response == 1:
            # select the posterior that corresponds to the stimulus intensity of lowest entropy
            self.pdf = self.posteriorTplus1success[Ellipsis, self.minEntropyInd]
        elif response == 0:
            self.pdf = self.posteriorTplus1failure[Ellipsis, self.minEntropyInd]

        # normalize the pdf
        self.pdf = self.pdf / np.sum(self.pdf)

        # Marginalized probabilities per parameter
        if self.gammaEQlambda:
            self.pThreshold = np.sum(self.pdf, axis=(1, 2))
            self.pSlope = np.sum(self.pdf, axis=(0, 2))
            self.pLapse = np.sum(self.pdf, axis=(0, 1))
            self.pGuess = self.pLapse
        else:
            self.pThreshold = np.sum(self.pdf, axis=(1, 2, 3))
            self.pSlope = np.sum(self.pdf, axis=(0, 2, 3))
            self.pLapse = np.sum(self.pdf, axis=(0, 1, 2))
            self.pGuess = np.sum(self.pdf, axis=(0, 1, 3))

        # Distribution means as expected values of parameters
        self.eThreshold = np.sum(np.multiply(self.threshold, self.pThreshold))
        self.eSlope = np.sum(np.multiply(self.slope, self.pSlope))
        self.eLapse = np.sum(np.multiply(self.lapseRate, self.pLapse))
        self.eGuess = np.sum(np.multiply(self.guessRate, self.pGuess))

        # Distribution std of parameters
        self.stdThreshold = np.sqrt(np.sum(np.multiply((self.threshold - self.eThreshold) ** 2, self.pThreshold)))
        self.stdSlope = np.sqrt(np.sum(np.multiply((self.slope - self.eSlope) ** 2, self.pSlope)))
        self.stdLapse = np.sqrt(np.sum(np.multiply((self.lapseRate - self.eLapse) ** 2, self.pLapse)))
        self.stdGuess = np.sqrt(np.sum(np.multiply((self.guessRate - self.eGuess) ** 2, self.pGuess)))

        # Start calculating the next minimum entropy stimulus
        
        if self.thread:
            threading.Thread(target=self.minEntropyStim).start()
        else:
            self.minEntropyStim()
            
# This works on ubuntu, not on Windows
timestamp = time.strftime("%Y%m%d_%H:%M:%S")

# If running on a Windows PC, run the following
# timestamp = time.strftime("%Y%m%d_%H_%M_%S")

# If running on an android device, set the right path to save the JSON file
if platform == 'android':
    from jnius import autoclass, cast, JavaException

    try:
        PythonActivity = autoclass('org.kivy.android.PythonActivity')
    except JavaException:
        PythonActivity = autoclass('org.renpy.android.PythonActivity')

    Environment = autoclass('android.os.Environment')
    context = cast('android.content.Context', PythonActivity.mActivity)
    private_storage = context.getExternalFilesDir(Environment.getDataDirectory().getAbsolutePath()).getAbsolutePath()

    store = JsonStore(".".join([private_storage, timestamp, 'json']))

# This is mainly for testing on a Linux Desktop
else:
    store = JsonStore(".".join([timestamp, 'json']))

# Prepare dictionaries to save information
subj_info = {}
subj_anth = {}
subj_trial_info = {}

# These are Psi-Marginal Staircase related parameters
# mu = threshold parameter
# sigma = slope parameter
# StimLevels = delta angle
ntrials = 50
mu = np.linspace(0, 15, 61)
sigma = np.linspace(0.05, 1, 21)
lapse = np.linspace(0, 0.1, 15)
guessRate = 0.5
# 5.0 degrees deviation means the exact spot of the center of an index finger - skipped
stimLevels = np.concatenate((np.arange(0, 5, 0.1), np.arange(5.1, 10, 0.1), np.arange(10, 16, 1)))

thresholdPrior = ('normal', 13, 3)
slopePrior = ('gamma', 2, 0.3)
lapsePrior = ('beta', 2, 20)

psi_obj = Psi(stimLevels, Pfunction = 'Gumbel', nTrials = ntrials, threshold = mu, thresholdPrior = thresholdPrior, slope = sigma, slopePrior = slopePrior, guessRate = guessRate, guessPrior = ('uniform', None), lapseRate = lapse, lapsePrior = lapsePrior, marginalize = True)

class CalibrationScreen(Screen):

    # Popup window
    def show_popup(self):

        the_popup = CalibPopup(title = "READ IT", size_hint = (None, None), size = (400, 400))
        the_popup.open()

class CalibPopup(Popup):
    pass

class ParamPopup(Popup):
    pass

class ParamInputScreenOne(Screen):

    male = ObjectProperty(True)
    female = ObjectProperty(False)
    right = ObjectProperty(True)
    left = ObjectProperty(False)

    gender = ObjectProperty(None)
    handed_chk = ObjectProperty(False)

    # Popup window to check if everything is saved properly
    def show_popup(self):

        the_popup = ParamPopup(title = "READ IT", size_hint = (None, None), size = (400, 400))

        # Check if any of the parameter inputs is missing!
        if any([self.pid_text_input.text == "", self.age_text_input.text == "", self.gender == None, self.handed_chk == False]) is True:
            the_popup.argh.text = "Value Missing!"
            the_popup.open()
        else:
            global subid
            subid = "_".join(["SUBJ", self.pid_text_input.text])
            global subj_info
            subj_info = {'age' : self.age_text_input.text, 'gender' : self.gender, 'right_used' : self.ids.rightchk.active}
            self.parent.current = "param_screen_two"

    def if_active_m(self, state):
        if state:
            # Whill change the orientation of the testscreen's colorscreen
            self.gender = "M"

    def if_active_f(self, state):
        if state:
            self.gender = "F"

    def if_active_r(self, state):
        if state:
            # Will change the orientation of the testscreen's colorscreen
            self.parent.ids.testsc.handedness.dir = 1
            #self.parent.ids.testsc.handedness.degree = -35

            # Just for fool-proof
            self.handed_chk = True

    def if_active_l(self, state):
        if state:
            self.parent.ids.testsc.handedness.dir = -1
            #self.parent.ids.testsc.handedness.degree = 35

            # Just for fool-proof
            self.handed_chk = True

class ParamInputScreenTwo(Screen):

    # Popup window to check if everything is entered
    def show_popup2(self):

        the_popup = ParamPopup(title = "READ IT", size_hint = (None, None), size = (400, 400))

        # Check if any of the parameter inputs is missing!
        if any([self.flen_text_input.text == "", self.fwid_text_input.text == "", self.initd_text_input.text == "", self.mprad_text_input.text == ""]):
            the_popup.argh.text = "Something's missing!"
            the_popup.open()
        else:
            global subj_anth
            subj_anth = {'flen' : self.flen_text_input.text, 'fwid' : self.fwid_text_input.text, 'init_step' : self.initd_text_input.text, 'MPJR' : self.mprad_text_input.text}

            # Give the mp joint radius input to draw the test screen display
            self.parent.ids.testsc.handedness.mprad = self.mprad_text_input.text
            self.parent.current = "test_screen"

class TestScreen(Screen):

    handedness = ObjectProperty(None)

    def __init__(self, **kwargs):
        super(TestScreen, self).__init__(**kwargs)
        self.rgblist1 = [(1, 0, 0, 1), (1, 1, 0, 1), (0, 1, 0, 1)]
        self.rgblist2 = [(0, 0, 1, 1), (0.5, 0, 1, 1), (1, 0.56, 0.75, 1)]
        self.rgbindex = 0
        # checking if the reverse is happening
        self.prev_choice = list()
        # session number
        self.session_num = 0
        # check the trial number(within a session)
        self.trial_num = 0
        # Keep the record of total trials(regardless of session)
        self.trial_total = 0

        self.mov_angle = psi_obj.xCurrent

    # changes the color of the buttons as well as the screen
    def change_col_setting(self):
        rgb_index = randrange(0, 3, 1)
        while rgb_index == self.rgbindex:
            rgb_index = randrange(0, 3, 1)
        self.ids.cw.bg_color_after = self.rgblist1[rgb_index]
        self.ids.cw.bg_color_before = self.rgblist2[rgb_index]
        self.ids._more_left.background_normal = ''
        self.ids._more_left.background_color = self.ids.cw.bg_color_after
        self.ids._more_right.background_normal = ''
        self.ids._more_right.background_color = self.ids.cw.bg_color_before
        self.rgbindex = rgb_index

    # keep track of reversals
    def track_choices(self, response):
        self.prev_choice.append(response)

    def where_is_your_finger(self, rel_pos):

        # change the colors of the screen
        self.change_col_setting()

        # Add the current choice, check if reversal is happening
        self.track_choices(rel_pos)

        # Save the current degree
        degree_current = self.ids.cw.degree

        # Check if the respons('on the left' or 'on the right') is correct
        # Get the current third x-coordinate of the quadrilateral, or the fourth point of the quadrilateral
        # Compare it with the true third x-coordinate of the quadrilateral
        # If the current x-coordinate is greater than the true value, the correct answer should be "left"
        # If the current x-coordinate is smaller than the true value, the correct answer should be "right"
        # If neither, the response is "on_the_spot"
        x_coord_current = self.ids.cw.quad_points[4]
        if x_coord_current > self.ids.cw.x_correct:
            correct_ans = "left"
        elif x_coord_current < self.ids.cw.x_correct:
            correct_ans = "right"
        else:
            correct_ans = "on_the_spot"

        # Compare if the answer is correct
        right_or_wrong = int(rel_pos == correct_ans)
        global psi_obj
        psi_obj.addData(right_or_wrong)
        while psi_obj.xCurrent is None:
            pass

        # next step deviation angle
        if rel_pos == 'left':

            # Set the left limit
            if (self.ids.cw.quad_points[6] + self.ids.cw.height*math.tan(math.radians(psi_obj.xCurrent)) < self.ids.cw.x):
                self.ids.cw.degree = math.degrees(math.atan((self.ids.cw.x - self.ids.cw.quad_points[6]) / self.ids.cw.height))
            else:
                self.ids.cw.degree = float(psi_obj.xCurrent)

        elif rel_pos == 'right':

            # Set the right limit
            if (self.ids.cw.quad_points[6] + self.ids.cw.height*math.tan(math.radians(psi_obj.xCurrent)) > self.ids.cw.right):
                self.ids.cw.degree = math.degrees(math.atan((self.ids.cw.right - self.ids.cw.quad_points[6]) / self.ids.cw.height))
            else:
                self.ids.cw.degree = float(psi_obj.xCurrent)

        #global subj_trial_info
        subj_trial_info["_".join(["TRIAL", str(self.trial_total)])] = {'session': self.session_num, 'trial_in_session': self.trial_num, 'reference(deg)': self.ids.cw.false_ref, 'offset(deg)': degree_current, 'correct_x': self.ids.cw.x_correct, 'x_coord_current': x_coord_current, 'correct_ans': correct_ans, 'response': self.prev_choice[-1], 'response_correct': right_or_wrong}

        self.trial_num += 1
        self.trial_total += 1


        # Print the trial number and the deviation angle(deg)
        # The value of the deviation angle is the angle between
        # - the vertical line that passes the MP joint
        # - the line that connects the MP joint and the upper right point of the quadrilateral
        #print("trial: ", self.trial_num, "session: ", self.session_num, "correct_ans: ", correct_ans, "rel_pos: ", rel_pos, "right_or_wrong: ", right_or_wrong, "Previous_delta_d: ", degree_current, "Next delta_d: ", self.ids.cw.degree, self.ids.cw.false_ref)

        if self.trial_num == 50:
            self.reset(self.session_num)

    def reset(self, session_num):
        # Renew the list of stored choices
        self.prev_choice = list()

        # Trial number renewed
        self.trial_num = 0

        # Psi marginal algorithm refreshed
        global psi_obj
        psi_obj = Psi(stimLevels, Pfunction = 'Gumbel', nTrials = ntrials, threshold = mu, thresholdPrior = thresholdPrior, slope = sigma, slopePrior = slopePrior, guessRate = guessRate, guessPrior = ('uniform', None), lapseRate = lapse, lapsePrior = lapsePrior, marginalize = True)

        # New display setting
        self.ids.cw.degree = float(psi_obj.xCurrent)

        if session_num == 0:
            # A new session begins
            self.session_num +=1

            # False reference moving to 45
            self.ids.cw.false_ref = 45
            # ... and the psi output will now be "added"
            self.ids.cw.degree_dir = 1

            # There's no turning back
            self.ids.layout.remove_widget(self.ids._backward)

            # The buttons would be disabled until an experimenter presses the 'resume' button
            self.ids._more_left.disabled = True
            self.ids._more_right.disabled = True

        # Only two sessions exist: 0 or 1
        # If session 1 finishes, you reset everthing to have a next subject
        else:
            # Dump everything to the store
            store.put(subid, subj_info = subj_info, subj_anth = subj_anth, subj_trial_info = subj_trial_info)

            self.session_num -= 1

            # False reference returning to 55
            self.ids.cw.false_ref = 55
            # ... and the psi output "subtracted"
            self.ids.cw.degree_dir = -1

            # Bring the back button again
            self.ids.layout.add_widget(self.ids._backward)

            # Total trial count reset to 0
            self.trial_total = 0

            # Go to the outcome screen
            self.parent.current = "outcome_screen"

class OutcomeScreen(Screen):

    def start_a_new_subject(self):
        self.parent.current = "param_screen_one"

class screen_manager(ScreenManager):
    pass

class ProprioceptiveApp(App):

    def build(self):
        return screen_manager(transition=FadeTransition())

if __name__ == '__main__':
    ProprioceptiveApp().run()
