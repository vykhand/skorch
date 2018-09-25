""" Callbacks related to training progress. """

import pickle
import warnings
from fnmatch import fnmatch
from functools import partial
from itertools import product

import numpy as np
from skorch.callbacks import Callback
from skorch.exceptions import SkorchException
from skorch.utils import noop
from skorch.utils import open_file_like
from skorch.utils import freeze_parameter
from skorch.utils import unfreeze_parameter


__all__ = ['Checkpoint', 'EarlyStopping', 'ParamMapper', 'Freezer',
           'Unfreezer', 'Initializer']


class Checkpoint(Callback):
    """Save the model during training if the given metric improved.

    This callback works by default in conjunction with the validation
    scoring callback since it creates a ``valid_loss_best`` value
    in the history which the callback uses to determine if this
    epoch is save-worthy.

    You can also specify your own metric to monitor or supply a
    callback that dynamically evaluates whether the model should
    be saved in this epoch.

    Some or all of the following can be saved:

      - model parameters (see ``f_params`` parameter);
      - training history (see ``f_history`` parameter);
      - entire model object (see ``f_pickle`` parameter).

    You can implement your own save protocol by subclassing
    ``Checkpoint`` and overriding :func:`~Checkpoint.save_model`.

    This callback writes a bool flag to the history column
    ``event_cp`` indicating whether a checkpoint was created or not.

    Example:

    >>> net = MyNet(callbacks=[Checkpoint()])
    >>> net.fit(X, y)

    Example using a custom monitor where models are saved only in
    epochs where the validation *and* the train losses are best:

    >>> monitor = lambda net: all(net.history[-1, (
    ...     'train_loss_best', 'valid_loss_best')])
    >>> net = MyNet(callbacks=[Checkpoint(monitor=monitor)])
    >>> net.fit(X, y)

    Parameters
    ----------
    target : deprecated

    monitor : str, function, None
      Value of the history to monitor or callback that determines
      whether this epoch should lead to a checkpoint. The callback
      takes the network instance as parameter.

      In case ``monitor`` is set to ``None``, the callback will save
      the network at every epoch.

      **Note:** If you supply a lambda expression as monitor, you cannot
      pickle the wrapper anymore as lambdas cannot be pickled. You can
      mitigate this problem by using importable functions instead.

    f_params : file-like object, str, None (default='params.pt')
      File path to the file or file-like object where the model
      parameters should be saved. Pass ``None`` to disable saving
      model parameters.

      If the value is a string you can also use format specifiers
      to, for example, indicate the current epoch. Accessible format
      values are ``net``, ``last_epoch`` and ``last_batch``.
      Example to include last epoch number in file name:

      >>> cb = Checkpoint(f_params="params_{last_epoch[epoch]}.pt")

    f_history : file-like object, str, None (default=None)
      File path to the file or file-like object where the model
      training history should be saved. Pass ``None`` to disable
      saving history.

      Supports the same format specifiers as ``f_params``.

    f_pickle : file-like object, str, None (default=None)
      File path to the file or file-like object where the entire
      model object should be pickled. Pass ``None`` to disable
      pickling.

      Supports the same format specifiers as ``f_params``.

    sink : callable (default=noop)
      The target that the information about created checkpoints is
      sent to. This can be a logger or ``print`` function (to send to
      stdout). By default the output is discarded.
    """
    def __init__(
            self,
            target=None,
            monitor='valid_loss_best',
            f_params='params.pt',
            f_history=None,
            f_pickle=None,
            sink=noop,
    ):
        if target is not None:
            warnings.warn(
                "target argument was renamed to f_params and will be removed "
                "in the next release. To make your code future-proof it is "
                "recommended to explicitly specify keyword arguments' names "
                "instead of relying on positional order.",
                DeprecationWarning)
            f_params = target
        self.monitor = monitor
        self.f_params = f_params
        self.f_history = f_history
        self.f_pickle = f_pickle
        self.sink = sink

    def on_epoch_end(self, net, **kwargs):
        if self.monitor is None:
            do_checkpoint = True
        elif callable(self.monitor):
            do_checkpoint = self.monitor(net)
        else:
            try:
                do_checkpoint = net.history[-1, self.monitor]
            except KeyError as e:
                raise SkorchException(
                    "Monitor value '{}' cannot be found in history. "
                    "Make sure you have validation data if you use "
                    "validation scores for checkpointing.".format(e.args[0]))

        if do_checkpoint:
            self.save_model(net)
            self._sink("A checkpoint was triggered in epoch {}.".format(
                len(net.history) + 1
            ), net.verbose)

        net.history.record('event_cp', bool(do_checkpoint))

    def save_model(self, net):
        """Save the model.

        This function saves some or all of the following:

          - model parameters;
          - training history;
          - entire model object.
        """
        if self.f_params:
            net.save_params(self._format_target(net, self.f_params))
        if self.f_history:
            net.save_history(self._format_target(net, self.f_history))
        if self.f_pickle:
            f_pickle = self._format_target(net, self.f_pickle)
            with open_file_like(f_pickle, 'wb') as f:
                pickle.dump(net, f)

    def _format_target(self, net, f):
        """Apply formatting to the target filename template."""
        if isinstance(f, str):
            return f.format(
                net=net,
                last_epoch=net.history[-1],
                last_batch=net.history[-1, 'batches', -1],
            )
        return f

    def _sink(self, text, verbose):
        #  We do not want to be affected by verbosity if sink is not print
        if (self.sink is not print) or verbose:
            self.sink(text)


class EarlyStopping(Callback):
    """Callback for stopping training when scores don't improve.

    Stop training early if a specified `monitor` metric did not
    improve in `patience` number of epochs by at least `threshold`.

    Parameters
    ----------
    monitor : str (default='valid_loss')
      Value of the history to monitor to decide whether to stop
      training or not.  The value is expected to be double and is
      commonly provided by scoring callbacks such as
      :class:`skorch.callbacks.EpochScoring`.

    lower_is_better : bool (default=True)
      Whether lower scores should be considered better or worse.

    patience : int (default=5)
      Number of epochs to wait for improvement of the monitor value
      until the training process is stopped.

    threshold : int (default=1e-4)
      Ignore score improvements smaller than `threshold`.

    threshold_mode : str (default='rel')
        One of `rel`, `abs`. Decides whether the `threshold` value is
        interpreted in absolute terms or as a fraction of the best
        score so far (relative)

    sink : callable (default=print)
      The target that the information about early stopping is
      sent to. By default, the output is printed to stdout, but the
      sink could also be a logger or :func:`~skorch.utils.noop`.

    """
    def __init__(
            self,
            monitor='valid_loss',
            patience=5,
            threshold=1e-4,
            threshold_mode='rel',
            lower_is_better=True,
            sink=print,
    ):
        self.monitor = monitor
        self.lower_is_better = lower_is_better
        self.patience = patience
        self.threshold = threshold
        self.threshold_mode = threshold_mode
        self.misses_ = 0
        self.dynamic_threshold_ = None
        self.sink = sink

    # pylint: disable=arguments-differ
    def on_train_begin(self, net, **kwargs):
        if self.threshold_mode not in ['rel', 'abs']:
            raise ValueError("Invalid threshold mode: '{}'"
                             .format(self.threshold_mode))
        self.misses_ = 0
        self.dynamic_threshold_ = np.inf if self.lower_is_better else -np.inf

    def on_epoch_end(self, net, **kwargs):
        current_score = net.history[-1, self.monitor]
        if not self._is_score_improved(current_score):
            self.misses_ += 1
        else:
            self.misses_ = 0
            self.dynamic_threshold_ = self._calc_new_threshold(current_score)
        if self.misses_ == self.patience:
            if net.verbose:
                self._sink("Stopping since {} has not improved in the last "
                           "{} epochs.".format(self.monitor, self.patience),
                           verbose=net.verbose)
            raise KeyboardInterrupt

    def _is_score_improved(self, score):
        if self.lower_is_better:
            return score < self.dynamic_threshold_
        return score > self.dynamic_threshold_

    def _calc_new_threshold(self, score):
        """Determine threshold based on score."""
        if self.threshold_mode == 'rel':
            abs_threshold_change = self.threshold * score
        else:
            abs_threshold_change = self.threshold

        if self.lower_is_better:
            new_threshold = score - abs_threshold_change
        else:
            new_threshold = score + abs_threshold_change
        return new_threshold

    def _sink(self, text, verbose):
        #  We do not want to be affected by verbosity if sink is not print
        if (self.sink is not print) or verbose:
            self.sink(text)



class ParamMapper(Callback):
    """Map arbitrary functions over module parameters filtered by pattern
    matching.

    In the simplest case the function is only applied once at
    the beginning of a given epoch (at ``on_epoch_begin``) but more complex
    execution schemes (e.g. periodic application) are possible using
    ``at`` and ``scheduler``.

    Notes
    -----
    When starting the training process after saving and loading a model,
    ``ParamMapper`` might re-initialize parts of your model when the
    history is not saved along with the model. To avoid this, in case
    you use ``ParamMapper`` (or subclasses, e.g. :class:`.Initializer`)
    and want to save your model make sure to either (a) use pickle,
    (b) save and load the history or (c) remove the parameter mapper
    callbacks before continuing training.

    Examples
    --------
    Initialize a layer on first epoch before the first training step:

    >>> init = partial(torch.nn.init.uniform_, a=0, b=1)
    >>> cb = ParamMapper('linear*.weight', at=1, fn=init)
    >>> net = Net(myModule, callbacks=[cb])

    Reset layer initialization if train loss reaches a certain value
    (e.g. re-initialize on overfit):

    >>> at = lambda net: net.history[-1, 'train_loss'] < 0.1
    >>> init = partial(torch.nn.init.uniform_, a=0, b=1)
    >>> cb = ParamMapper('linear0.weight', at=at, fn=init)
    >>> net = Net(myModule, callbacks=[cb])

    Periodically freeze and unfreeze all embedding layers:

    >>> def my_sched(net):
    ...    if len(net.history) % 2 == 0:
    ...        return skorch.utils.freeze_parameter
    ...    else:
    ...        return skorch.utils.unfreeze_parameter
    >>> cb = ParamMapper('embedding*.weight', schedule=my_sched)
    >>> net = Net(myModule, callbacks=[cb])

    Parameters
    ----------
    patterns : str or callable or list
      The pattern(s) to match parameter names against. Patterns are
      UNIX globbing patterns as understood by :func:`~fnmatch.fnmatch`.
      Patterns can also be callables which will get called with the
      parameter name and are regarded as a match when the callable
      returns a truthy value.

      This parameter also supports lists of str or callables so that
      one ``ParamMapper`` can match a group of parameters.

      Example: ``'linear*.weight'`` or ``['linear0.*', 'linear1.bias']``
      or ``lambda name: name.startswith('linear')``.

    fn : function
      The function to apply to each parameter separately.

    at : int or callable
      In case you specify an integer it represents the epoch number the
      function ``fn`` is applied to the parameters, in case ``at`` is
      a function it will receive ``net`` as parameter and the function
      is applied to the parameter once ``at`` returns ``True``.

    schedule : callable or None
      If specified this callable supersedes the static ``at``/``fn``
      combination by dynamically returning the function that is applied
      on the matched parameters. This way you can, for example, create a
      schedule that periodically freezes and unfreezes layers.

      The callable's signature is ``schedule(net: NeuralNet) -> callable``.

    """
    def __init__(self, patterns, fn=noop, at=1, schedule=None):
        self.at = at
        self.fn = fn
        self.schedule = schedule
        self.patterns = patterns

    def initialize(self):
        if not self.schedule:
            self.schedule = self._default_schedule

        if not isinstance(self.patterns, (list, tuple)):
            self.patterns = [self.patterns]

        if isinstance(self.at, int):
            if self.at <= 0:
                raise ValueError(
                    'Invalid value for `at` (at={}). The first possible '
                    'epoch number is 1.'.format(self.at))
            self.at = partial(self._epoch_at, epoch=self.at)

        return self

    def named_parameters(self, net):
        return net.module_.named_parameters()

    def filter_parameters(self, patterns, params):
        pattern_fns = (
            pattern if callable(pattern) else partial(fnmatch, pat=pattern)
            for pattern in patterns
        )
        for pattern_fn, (name, param) in product(pattern_fns, params):
            if pattern_fn(name):
                yield name, param

    def _default_schedule(self, net):
        if self.at(net):
            return self.fn
        return noop

    def _epoch_at(self, net, epoch=1):
        return len(net.history) == epoch

    def on_epoch_begin(self, net, **kwargs):
        params = self.named_parameters(net)
        params = self.filter_parameters(self.patterns, params)
        map_fn = self.schedule(net)

        for _, p in params:
            map_fn(p)



class Freezer(ParamMapper):
    """Freeze matching parameters at the start of the first epoch. You may
    specify a specific point in time (either by epoch number or using a
    callable) when the parameters are frozen using the ``at`` parameter.

    See :class:`.ParamMapper` for details.
    """
    def __init__(self, *args, **kwargs):
        kwargs['at'] = kwargs.get('at', 1)
        kwargs['fn'] = kwargs.get('fn', freeze_parameter)
        super().__init__(*args, **kwargs)


class Unfreezer(ParamMapper):
    """Inverse operation of :class:`.Freezer`."""
    def __init__(self, *args, **kwargs):
        kwargs['at'] = kwargs.get('at', 1)
        kwargs['fn'] = kwargs.get('fn', unfreeze_parameter)
        super().__init__(*args, **kwargs)


class Initializer(ParamMapper):
    """Apply any function on matching parameters in the first epoch.

    Examples
    --------

    Use ``Initializer`` to initialize all dense layer weights with
    values sampled from an uniform distribution on the beginning of
    the first epoch:

    >>> init_fn = partial(torch.nn.init.uniform_, a=-1e-3, b=1e-3)
    >>> cb = Initializer('dense*.weight', fn=init_fn)
    >>> net = Net(myModule, callbacks=[cb])
    """
    def __init__(self, *args, **kwargs):
        kwargs['at'] = kwargs.get('at', 1)
        super().__init__(*args, **kwargs)
